"""Write node: drafts the personalised sales email.

Strict rules are set in the prompt AND validated in code (tone, length, opt-out,
spam triggers). On a violation the email is regenerated once with explicit
feedback. An LLM judge then checks for repeated ideas across the three sentences.
If the retry still isn't fully compliant, the email is kept but flagged for review.

Prompts, validation messages and the email itself are in Dutch: the emails go to
Flemish businesses.
"""

from __future__ import annotations

import logging
import re
from typing import Protocol

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_anthropic import ChatAnthropic

from src.config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL
from src.state import LeadState

logger = logging.getLogger(__name__)

SIGNATURE = """Vragen? Stuur gerust een berichtje via WhatsApp of mail — ik reageer snel.

Met vriendelijke groeten,

Domien Darmont
Darmont Digital — AI-automatisering voor zelfstandigen
darmontdigital@gmail.com
WhatsApp: 0497 66 94 07
https://www.linkedin.com/in/domien-darmont-b53b7640b/"""

SYSTEM_PROMPT = """Je bent een Vlaamse B2B-copywriter die gepersonaliseerde cold-outreach mails schrijft voor Domien Darmont.

AFZENDER: Domien Darmont — AI-engineer, opleiding bij Ironhack. Hij kijkt bij zelfstandigen en kleine
bedrijven mee welk repetitief werk geautomatiseerd kan worden — dat kan van alles zijn: oproepen opvangen,
mails beantwoorden, afspraken inplannen, offertes genereren, vragen doorzoeken in documenten, leads
opvolgen. Mensen weten zelf vaak niet hoe ze AI kunnen inzetten. Domien toont dat concreet.

Je krijgt:
- IJSBREKER: een specifiek detail van hun website — bewijst dat je de site écht las
- HAAKJE: het concrete handmatige knelpunt dat hen tijd of klanten kost

STRUCTUUR (exact 3 zinnen, GEEN aanhef, GEEN handtekening — die worden automatisch toegevoegd):
- Zin 1: IJSBREKER — verwerk het concrete sitedetail
  VERMIJD het vaste sjabloon "[sitedetail] + is/bewijst/toont/zegt/speelt in op [waardeoordeel]"
  (bv. "Die belofte van 30 minuten... zegt...", "Dat jullie... is een sterk fundament",
  "Meer dan 1000 geopende deuren... is..." — dit patroon in élke mail herhalen leest meteen als
  AI-gegenereerd, ook al is elk detail zelf uniek). Varieer de zinsbouw écht per mail: soms een
  gewone vaststelling zonder compliment-clausule, soms het detail terloops verwerkt in een zin die
  ergens anders naartoe gaat, soms een korte vraag. Begin niet elke mail met "Die/Dat/Een/Met/Als/
  Jullie/Meer dan" — wissel onderwerp en zinsstart af.
- Zin 2: HAAKJE → pijn — benoem het handmatige knelpunt dat hen tijd of klanten kost, assertief
  (geen "lijkt", "vraag ik me af", "wellicht" — wees direct)
- Zin 3: schrijf in de EERSTE PERSOON — kies de winst die past bij het HAAKJE:
    • Haakje = telefonisch bereikbaar / boekingen mislopen → "geen potentiële klant meer mislopen"
    • Haakje = kennissector / vragen / documenten → "sneller antwoord geven aan je klanten"
    • Altijd ook: "tijd besparen op repetitief werk"
  Verweef de gekozen winst en "tijd besparen" in één vloeiende zin. Geen opsomming. Sluit af
  met een open uitnodiging zonder druk. GEEN "15 minuten".
  Bv (bereikbaarheid): "Bij Darmont Digital kijk ik mee welk repetitief werk ik voor je kan
  automatiseren — zodat je geen potentiële klant meer mist en tijd wint voor wat er écht toe doet
  — als je wil, bekijk ik graag wat er in jouw geval mogelijk is."
  Bv (kennissector): "Bij Darmont Digital kijk ik mee hoe ik je kenniswerk kan automatiseren
  — zodat je sneller antwoord geeft en minder tijd verliest aan opzoekwerk — als je wil, bekijk
  ik graag wat er concreet mogelijk is."

REGELS:
- Vlot Vlaams, "je/jullie", NOOIT "u/uw" of "omgeving van"
- Zin 3 ALTIJD in eerste persoon (ik/we) — NOOIT derde persoon
- Schrijf correct Nederlands: ë, ï, é, è zijn gewone Nederlandse letters, gebruik ze correct
  (potentiële, geïnteresseerd, financiële, etc.) — vermijd alleen Cyrillische lookalikes
- VERBODEN: "mooie website", "gratis", "gegarandeerd", "klik hier", uitroeptekens, "automatisering" meer dan 1x
- Zin 3 NOOIT een opsomming van AI-tools — benoem de uitkomst, niet de technologie
- KRITISCH: elke zin moet een UNIEK idee brengen — herhaal nooit dezelfde gedachte
- Schrijf ALLEEN de 3 zinnen — voeg GEEN slotzin of extra commentaar toe

Formaat (niets anders):
ONDERWERP: <max 6 woorden, specifiek voor dit bedrijf>
MAIL: <zin 1>

<zin 2>

<zin 3>"""

REGENERATE_INSTRUCTION = (
    "Je vorige poging voldeed niet aan de regels ({problems}). Herschrijf de mail van "
    "nul af en volg de regels nu strikt."
)

_FORBIDDEN_FORMAL = re.compile(r"\b(u|uw)\b", re.IGNORECASE)
_SPAM_TRIGGER_PATTERN = re.compile(
    r"\b(gratis|100%\s*gratis|gegarandeerd|klik\s*hier|actie\s*nu|nu\s*kopen|"
    r"beperkte\s*tijd|exclusieve\s*aanbieding|win\s*nu|korting|"
    r"laatste\s*kans)\b",
    re.IGNORECASE,
)
_GENERIC_COMPLIMENT_PATTERN = re.compile(r"mooie\s+website", re.IGNORECASE)
_AUTOMATION_PATTERN = re.compile(r"automatiser\w*", re.IGNORECASE)
_RESPONSE_PATTERN = re.compile(
    r"ONDERWERP:\s*(?P<subject_text>.+?)\s*\n+\s*MAIL:\s*(?P<mail>.+)",
    re.IGNORECASE | re.DOTALL,
)

_EXTRA_LINE = "Van chatbot en AI-telefonist tot WhatsApp-assistent en automatische opvolging — in principe is elk repetitief proces te automatiseren."


class ChatLLM(Protocol):
    async def ainvoke(self, messages: list) -> object: ...


def _build_llm() -> ChatLLM:
    return ChatAnthropic(model=ANTHROPIC_MODEL, api_key=ANTHROPIC_API_KEY, temperature=0.6)


def _validate_mail(mail: str) -> list[str]:
    """Validate only the three-sentence body (without greeting/signature)."""
    problems: list[str] = []
    # only count real sentences in the body (before the signature)
    body = mail.split("Met vriendelijke groeten")[0].strip()
    # also strip the extra line in case the LLM added it
    body = body.replace(_EXTRA_LINE, "").strip()
    sentences = [z.strip() for z in re.split(r"(?<=[.!?])\s+", body) if z.strip()]
    if len(sentences) > 3:
        problems.append(f"te veel zinnen in body ({len(sentences)} > 3)")
    # detect literally identical sentences
    unique_items = set(z.lower() for z in sentences)
    if len(unique_items) < len(sentences):
        problems.append("bevat letterlijk identieke zinnen")
    if _FORBIDDEN_FORMAL.search(body):
        problems.append("gebruikt 'u'/'uw' in plaats van 'je/jullie'")
    if "omgeving van" in body.lower():
        problems.append("gebruikt verboden uitdrukking 'omgeving van'")
    if _SPAM_TRIGGER_PATTERN.search(body):
        problems.append("gebruikt een klassiek spam-triggerwoord (bv. 'gratis', 'klik hier')")
    if body.count("!") > 1:
        problems.append("te veel uitroeptekens (max 1)")
    if _GENERIC_COMPLIMENT_PATTERN.search(body):
        problems.append("gebruikt verboden uitdrukking 'mooie website'")
    if len(_AUTOMATION_PATTERN.findall(body)) > 1:
        problems.append("gebruikt 'automatisering' meer dan één keer")
    return problems


_JUDGE_PROMPT = (
    "Lees deze e-mailbody (3 korte zinnen).\n"
    "Controleer of zinnen 1, 2 en 3 elk een UNIEK inhoudelijk idee brengen.\n"
    "Let op: zin 3 eindigt altijd met een uitnodiging om samen te kijken — dat is GEEN herhaling.\n"
    "Vlag alleen als zin 1 en 2 of zin 1 en 3 dezelfde kern-observatie of pijn herhalen.\n"
    "Antwoord UITSLUITEND met:\n"
    "OK\n"
    "of, bij echte inhoudelijke herhaling:\n"
    "PROBLEEM: <wat er herhaald wordt in max 10 woorden>\n\n"
    "BODY:\n{body}"
)

async def _judge_body(llm: ChatLLM, body: str) -> str | None:
    """LLM judge: checks the three-sentence body for repeated ideas.
    Returns None when every sentence is unique, otherwise the problem description."""
    prompt = _JUDGE_PROMPT.format(body=body)
    response = await llm.ainvoke([HumanMessage(content=prompt)])
    result = str(response.content).strip()
    if result.upper().startswith("PROBLEEM") or result.upper().startswith("PROBLEM"):
        return result
    return None


# Cyrillic lookalike -> Latin equivalent (the model occasionally emits these)
_CYRILLIC_TO_LATIN = str.maketrans({
    'а': 'a', 'е': 'e', 'о': 'o', 'р': 'p', 'с': 'c', 'х': 'x',
    'і': 'i', 'у': 'y', 'в': 'v', 'п': 'p', 'н': 'h', 'м': 'm',
    'т': 't', 'к': 'k', 'л': 'l', 'б': 'b', 'г': 'g', 'д': 'd',
    'ж': 'zh', 'з': 'z', 'й': 'y', 'ф': 'f', 'ц': 'ts', 'ч': 'ch',
    'ш': 'sh', 'щ': 'sch', 'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e',
    'ю': 'yu', 'я': 'ya', 'ё': 'yo', 'и': 'i',
    'А': 'A', 'Е': 'E', 'О': 'O', 'Р': 'P', 'С': 'C', 'Х': 'X',
    'В': 'B', 'П': 'P', 'Н': 'H', 'М': 'M', 'Т': 'T', 'К': 'K',
})

def _sanitize(text: str) -> str:
    """Replace Cyrillic lookalike characters with their Latin equivalent."""
    return text.translate(_CYRILLIC_TO_LATIN)


def strip_body(raw_mail: str) -> str:
    """Remove greeting, signature and extra line from an assembled email.
    Use this in scripts that read emails back from Excel before reassembling, to avoid duplicates."""
    mail = re.sub(r"^Beste[^\n]+,\s*\n+", "", raw_mail.strip(), flags=re.MULTILINE)
    # cut at the earliest signature marker
    cut = len(mail)
    for marker in ("Vragen? Stuur", "Met vriendelijke groeten"):
        idx = mail.find(marker)
        if idx != -1:
            cut = min(cut, idx)
    mail = mail[:cut].strip()
    mail = mail.replace(_EXTRA_LINE, "").strip()
    return mail


def _assemble_mail(contact_name: str, body: str) -> str:
    """Add greeting, extra line and signature to the generated body."""
    # strip boilerplate the LLM may have included, so it never appears twice
    clean = _sanitize(strip_body(body) if _EXTRA_LINE in body else body)
    return f"Beste {contact_name},\n\n{clean}\n\n{_EXTRA_LINE}\n\n{SIGNATURE}"


def clean_subject(subject_text: str) -> str:
    """Replace em dashes and other non-ASCII punctuation in the subject line with ASCII."""
    return subject_text.replace("—", "-").replace("–", "-").replace("‘", "'").replace("’", "'")


def _validate_subject_line(subject_text: str) -> list[str]:
    problems: list[str] = []
    if not subject_text:
        problems.append("geen onderwerpregel gegenereerd")
        return problems
    words = subject_text.split()
    if len(words) > 6:
        problems.append(f"onderwerpregel te lang ({len(words)} > 6 woorden)")
    return problems


_SUBJECT_ONLY_PATTERN = re.compile(
    r"ONDERWERP:\s*(?P<subject_text>.+?)\s*\n+(?P<mail>.+)",
    re.IGNORECASE | re.DOTALL,
)

def _parse_response(raw: str) -> tuple[str, str]:
    """Split the ONDERWERP/MAIL (subject/email) format; fall back to the full text as
    the email when the model ignored the format (must never crash the run)."""
    # try the full format first (ONDERWERP: ... MAIL: ...)
    match = _RESPONSE_PATTERN.search(raw)
    if match:
        return match.group("subject_text").strip(), match.group("mail").strip()
    # fallback: subject present but no "MAIL:" marker
    match = _SUBJECT_ONLY_PATTERN.search(raw)
    if match:
        return match.group("subject_text").strip(), match.group("mail").strip()
    return "", raw.strip()


async def _generate(
    llm: ChatLLM, lead: LeadState, extra_instruction: str | None = None
) -> tuple[str, str]:
    hook = lead["hook"] or lead["icebreaker"]
    icebreaker = lead.get("icebreaker", "")
    content = f"Bedrijf: {lead['company_name']}, sector: {lead['niche']}.\n"
    if icebreaker:
        content += f"IJSBREKER (specifiek sitedetail): {icebreaker}\n"
    content += f"HAAKJE (handmatig knelpunt): {hook}\n"
    if extra_instruction:
        content += f"\n{extra_instruction}\n"
    response = await llm.ainvoke(
        [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=content)]
    )
    return _parse_response(str(response.content).strip())


async def write_mails(leads: list[LeadState], llm: ChatLLM | None = None) -> list[LeadState]:
    """Write the email and subject line for every lead with status 'ok'; mutates the leads in place."""
    active_llm = llm if llm is not None else _build_llm()

    for lead in leads:
        if lead["status"] != "ok":
            continue
        try:
            subject_text, mail = await _generate(active_llm, lead)
            problems = _validate_mail(mail) + _validate_subject_line(subject_text)
            if problems:
                logger.info(
                    "Email for %s breaks the rules (%s) - regenerating",
                    lead["company_name"],
                    "; ".join(problems),
                )
                subject_text, mail = await _generate(
                    active_llm,
                    lead,
                    REGENERATE_INSTRUCTION.format(problems="; ".join(problems)),
                )
                problems = _validate_mail(mail) + _validate_subject_line(subject_text)
                if problems:
                    logger.warning(
                        "Email for %s still breaks the rules after regeneration (%s) - "
                        "manual review especially important",
                        lead["company_name"],
                        "; ".join(problems),
                    )
                    lead["error_reason"] = "Email validation: " + "; ".join(problems)

            # LLM judge: check for repeated ideas across the three sentences
            repetition = await _judge_body(active_llm, mail)
            if repetition:
                logger.info(
                    "LLM judge: repetition in email for %s (%s) - regenerating",
                    lead["company_name"],
                    repetition,
                )
                subject_text, mail = await _generate(
                    active_llm,
                    lead,
                    f"Vorige poging bevatte herhalingen: {repetition}. "
                    "Schrijf 3 volledig unieke zinnen zonder enige herhaling van dezelfde gedachte of formulering.",
                )
                repetition_retry = await _judge_body(active_llm, mail)
                if repetition_retry:
                    logger.warning(
                        "Still repetitive after regeneration for %s (%s) - keeping the email",
                        lead["company_name"],
                        repetition_retry,
                    )
                    lead["error_reason"] = (lead.get("error_reason") or "") + f" | Repetition: {repetition_retry}"

            lead["mail"] = _assemble_mail(lead["company_name"], mail)
            lead["subject_line"] = subject_text
        except Exception as exc:  # noqa: BLE001 - one failed email must not stop the run
            lead["status"] = "error"
            lead["error_reason"] = f"Email generation error: {exc}"
            logger.warning("Email generation error for %s: %s", lead["company_name"], exc)

    return leads
