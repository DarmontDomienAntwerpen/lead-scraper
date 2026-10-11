"""OPTIMIZER: write-node — schrijft de gepersonaliseerde verkoopmail.

Strikte regels worden in de prompt opgelegd EN in code gevalideerd
(taal/lengte/opt-out). Bij een schending wordt één keer hergenereerd met
expliciete feedback. Daarna controleert een LLM-rechter op inhoudelijke
herhalingen (dubbele zinnen, herhaalde ideeën). Slaagt de hergeneratie
nog niet volledig, dan wordt de mail toch meegenomen maar gelogd.
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
    "Je vorige poging voldeed niet aan de regels ({problemen}). Herschrijf de mail van "
    "nul af en volg de regels nu strikt."
)

_VERBODEN_FORMEEL = re.compile(r"\b(u|uw)\b", re.IGNORECASE)
_SPAM_TRIGGER_PATTERN = re.compile(
    r"\b(gratis|100%\s*gratis|gegarandeerd|klik\s*hier|actie\s*nu|nu\s*kopen|"
    r"beperkte\s*tijd|exclusieve\s*aanbieding|win\s*nu|korting|"
    r"laatste\s*kans)\b",
    re.IGNORECASE,
)
_MOOIE_WEBSITE_PATTERN = re.compile(r"mooie\s+website", re.IGNORECASE)
_AUTOMATISERING_PATTERN = re.compile(r"automatiser\w*", re.IGNORECASE)
_RESPONSE_PATTERN = re.compile(
    r"ONDERWERP:\s*(?P<onderwerp>.+?)\s*\n+\s*MAIL:\s*(?P<mail>.+)",
    re.IGNORECASE | re.DOTALL,
)

_EXTRA_REGEL = "Van chatbot en AI-telefonist tot WhatsApp-assistent en automatische opvolging — in principe is elk repetitief proces te automatiseren."


class ChatLLM(Protocol):
    async def ainvoke(self, messages: list) -> object: ...


def _build_llm() -> ChatLLM:
    return ChatAnthropic(model=ANTHROPIC_MODEL, api_key=ANTHROPIC_API_KEY, temperature=0.6)


def _validate_mail(mail: str) -> list[str]:
    """Valideert alleen de 3-zins body (zonder aanhef/handtekening)."""
    problemen: list[str] = []
    # tel alleen echte zinnen in het body-deel (vóór de handtekening)
    body = mail.split("Met vriendelijke groeten")[0].strip()
    # Strip ook de extra_regel als LLM die per ongeluk toevoegde
    body = body.replace(_EXTRA_REGEL, "").strip()
    zinnen = [z.strip() for z in re.split(r"(?<=[.!?])\s+", body) if z.strip()]
    if len(zinnen) > 3:
        problemen.append(f"te veel zinnen in body ({len(zinnen)} > 3)")
    # Detecteer letterlijk identieke zinnen
    unieke = set(z.lower() for z in zinnen)
    if len(unieke) < len(zinnen):
        problemen.append("bevat letterlijk identieke zinnen")
    if _VERBODEN_FORMEEL.search(body):
        problemen.append("gebruikt 'u'/'uw' in plaats van 'je/jullie'")
    if "omgeving van" in body.lower():
        problemen.append("gebruikt verboden uitdrukking 'omgeving van'")
    if _SPAM_TRIGGER_PATTERN.search(body):
        problemen.append("gebruikt een klassiek spam-triggerwoord (bv. 'gratis', 'klik hier')")
    if body.count("!") > 1:
        problemen.append("te veel uitroeptekens (max 1)")
    if _MOOIE_WEBSITE_PATTERN.search(body):
        problemen.append("gebruikt verboden uitdrukking 'mooie website'")
    if len(_AUTOMATISERING_PATTERN.findall(body)) > 1:
        problemen.append("gebruikt 'automatisering' meer dan één keer")
    return problemen


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
    """LLM-rechter: controleert op inhoudelijke herhalingen in de 3-zins body.
    Geeft None terug als alles uniek is, anders de probleembeschrijving."""
    prompt = _JUDGE_PROMPT.format(body=body)
    response = await llm.ainvoke([HumanMessage(content=prompt)])
    result = str(response.content).strip()
    if result.upper().startswith("PROBLEEM") or result.upper().startswith("PROBLEM"):
        return result
    return None


# Cyrillische lookalike → Latijns equivalent (één-op-één mapping)
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
    """Vervang Cyrillische lookalikes door hun Latijnse equivalent."""
    return text.translate(_CYRILLIC_TO_LATIN)


def strip_body(raw_mail: str) -> str:
    """Strip aanhef, handtekening en extra_regel uit een samengestelde mail.
    Gebruik dit in scripts die mails lezen uit Excel vóór reassembly — zo nooit dubbels."""
    mail = re.sub(r"^Beste[^\n]+,\s*\n+", "", raw_mail.strip(), flags=re.MULTILINE)
    # Knip bij de vroegst voorkomende handtekening-marker
    cut = len(mail)
    for marker in ("Vragen? Stuur", "Met vriendelijke groeten"):
        idx = mail.find(marker)
        if idx != -1:
            cut = min(cut, idx)
    mail = mail[:cut].strip()
    mail = mail.replace(_EXTRA_REGEL, "").strip()
    return mail


def _assemble_mail(naam: str, body: str) -> str:
    """Voegt aanhef, extra context en handtekening toe aan de gegenereerde body."""
    # Strip boilerplate als de LLM het toch meenam, zodat het nooit dubbel staat
    clean = _sanitize(strip_body(body) if _EXTRA_REGEL in body else body)
    return f"Beste {naam},\n\n{clean}\n\n{_EXTRA_REGEL}\n\n{SIGNATURE}"


def clean_subject(onderwerp: str) -> str:
    """Vervangt em-dashes en andere non-ASCII leestekens door ASCII-equivalent in de onderwerpregel."""
    return onderwerp.replace("—", "-").replace("–", "-").replace("‘", "'").replace("’", "'")


def _validate_onderwerpregel(onderwerp: str) -> list[str]:
    problemen: list[str] = []
    if not onderwerp:
        problemen.append("geen onderwerpregel gegenereerd")
        return problemen
    woorden = onderwerp.split()
    if len(woorden) > 6:
        problemen.append(f"onderwerpregel te lang ({len(woorden)} > 6 woorden)")
    return problemen


_ONDERWERP_ONLY_PATTERN = re.compile(
    r"ONDERWERP:\s*(?P<onderwerp>.+?)\s*\n+(?P<mail>.+)",
    re.IGNORECASE | re.DOTALL,
)

def _parse_response(raw: str) -> tuple[str, str]:
    """Splitst het ONDERWERP/MAIL-formaat; valt terug op de volledige tekst als mail
    wanneer het model het formaat niet volgde (mag de run nooit laten crashen)."""
    # Probeer eerst het volledige formaat (ONDERWERP: ... MAIL: ...)
    match = _RESPONSE_PATTERN.search(raw)
    if match:
        return match.group("onderwerp").strip(), match.group("mail").strip()
    # Fallback: ONDERWERP aanwezig maar zonder "MAIL:" marker
    match = _ONDERWERP_ONLY_PATTERN.search(raw)
    if match:
        return match.group("onderwerp").strip(), match.group("mail").strip()
    return "", raw.strip()


async def _generate(
    llm: ChatLLM, lead: LeadState, extra_instructie: str | None = None
) -> tuple[str, str]:
    haakje = lead["haakje"] or lead["ijsbreker"]
    ijsbreker = lead.get("ijsbreker", "")
    content = f"Bedrijf: {lead['bedrijfsnaam']}, sector: {lead['niche']}.\n"
    if ijsbreker:
        content += f"IJSBREKER (specifiek sitedetail): {ijsbreker}\n"
    content += f"HAAKJE (handmatig knelpunt): {haakje}\n"
    if extra_instructie:
        content += f"\n{extra_instructie}\n"
    response = await llm.ainvoke(
        [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=content)]
    )
    return _parse_response(str(response.content).strip())


async def write_mails(leads: list[LeadState], llm: ChatLLM | None = None) -> list[LeadState]:
    """Schrijft de mail + onderwerpregel voor elke lead met status 'ok'; muteert de leads in-place."""
    active_llm = llm if llm is not None else _build_llm()

    for lead in leads:
        if lead["status"] != "ok":
            continue
        try:
            onderwerp, mail = await _generate(active_llm, lead)
            problemen = _validate_mail(mail) + _validate_onderwerpregel(onderwerp)
            if problemen:
                logger.info(
                    "Mail voor %s voldoet niet aan de regels (%s) - hergenereren",
                    lead["bedrijfsnaam"],
                    "; ".join(problemen),
                )
                onderwerp, mail = await _generate(
                    active_llm,
                    lead,
                    REGENERATE_INSTRUCTION.format(problemen="; ".join(problemen)),
                )
                problemen = _validate_mail(mail) + _validate_onderwerpregel(onderwerp)
                if problemen:
                    logger.warning(
                        "Mail voor %s voldoet ook na hergeneratie niet (%s) - manuele "
                        "review extra belangrijk hier",
                        lead["bedrijfsnaam"],
                        "; ".join(problemen),
                    )
                    lead["error_reden"] = "Mail-validatie: " + "; ".join(problemen)

            # LLM-rechter: check op inhoudelijke herhalingen over de 3 zinnen
            herhaling = await _judge_body(active_llm, mail)
            if herhaling:
                logger.info(
                    "LLM-rechter: herhaling in mail voor %s (%s) — hergenereren",
                    lead["bedrijfsnaam"],
                    herhaling,
                )
                onderwerp, mail = await _generate(
                    active_llm,
                    lead,
                    f"Vorige poging bevatte herhalingen: {herhaling}. "
                    "Schrijf 3 volledig unieke zinnen zonder enige herhaling van dezelfde gedachte of formulering.",
                )
                herhaling2 = await _judge_body(active_llm, mail)
                if herhaling2:
                    logger.warning(
                        "Nog steeds herhalingen na hergeneratie voor %s (%s) — mail toch gebruikt",
                        lead["bedrijfsnaam"],
                        herhaling2,
                    )
                    lead["error_reden"] = (lead.get("error_reden") or "") + f" | Herhaling: {herhaling2}"

            lead["mail"] = _assemble_mail(lead["bedrijfsnaam"], mail)
            lead["onderwerpregel"] = onderwerp
        except Exception as exc:  # noqa: BLE001 - een kapotte mail-generatie mag de run niet stoppen
            lead["status"] = "error"
            lead["error_reden"] = f"Mail-generatie-fout: {exc}"
            logger.warning("Mail-generatie-fout voor %s: %s", lead["bedrijfsnaam"], exc)

    return leads
