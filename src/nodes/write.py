"""OPTIMIZER: write-node — schrijft de gepersonaliseerde verkoopmail.

Strikte regels worden in de prompt opgelegd EN in code gevalideerd
(taal/lengte/opt-out). Bij een schending wordt één keer hergenereerd met
expliciete feedback. Slaagt de hergeneratie nog niet volledig, dan wordt de
mail toch meegenomen (de gebruiker leest sowieso alles manueel na) maar
loggen we een duidelijke waarschuwing zodat het opvalt bij review.
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

SYSTEM_PROMPT = """Je bent een Vlaamse B2B-copywriter (rol: OPTIMIZER) die korte, \
persoonlijke cold-outreach mails schrijft voor een AI-automatisering bureau dat \
zelfstandigen en KMO's helpt tijd te besparen met automatisering.

Je krijgt een HAAKJE: een concreet, waargenomen handmatig proces op de website van het \
bedrijf (bv. een mailto-adres in plaats van een gekoppeld formulier, geen onlineboeking, \
offertes die via mail aangevraagd worden). Bouw de mail rond DAT haakje - gebruik geen \
info die niet in het haakje of de sector/niche staat, en doe geen aannames die je niet \
kan zien op basis daarvan.

STRIKTE REGELS (niet onderhandelbaar):
- Taal: natuurlijk en vlot Vlaams. Spreek de lezer aan met "je"/"jullie". Gebruik NOOIT \
  "u", "uw" of de uitdrukking "omgeving van".
- Lengte: maximaal 4 zinnen in totaal, geen titel, geen aanhef ("Beste ..."), geen \
  afsluiter ("Met vriendelijke groeten").
- Zin 1: benoem het handmatige proces uit het haakje concreet en feitelijk.
- Zin 2: maak het tastbaar in tijd of foutgevoeligheid, zonder te overdrijven. Noem GEEN \
  concreet aantal uren of percentage besparing - dat verschilt te veel per bedrijf om \
  eerlijk te kunnen beweren; blijf bij de taak zelf, niet bij een cijfer.
- Zin 3: één lichte vraag of dit herkenbaar is bij hen - geen pitch, geen kant-en-klare \
  demo of oplossing beloven.
- Zin 4 (verplicht): een korte, duidelijke opt-out-zin (bv. "Laat het gerust weten als \
  je dit niet wil ontvangen.").
- Verboden: "ik zag jullie mooie website" (of varianten daarop), algemene verkooptaal, \
  beloftes over resultaat, en het woord "automatisering" maximaal één keer.
- Geen overdreven salesy taal, geen uitroeptekens-spam. Vermijd klassieke spam-triggerwoorden \
  zoals "gratis", "gegarandeerd", "klik hier", "actie nu", "beperkte tijd", "exclusieve aanbieding".
- Maximaal één uitroepteken in de hele mail (idealiter geen).

Geef je antwoord EXACT in dit formaat, niets anders (geen uitleg ervoor of erna):
ONDERWERP: <onderwerpregel van max 6 woorden die het haakje concreet benoemt, bv. "Offertes overtypen bij {bedrijfsnaam}?">
MAIL: <de platte mailtekst volgens de regels hierboven>"""

REGENERATE_INSTRUCTION = (
    "Je vorige poging voldeed niet aan de regels ({problemen}). Herschrijf de mail van "
    "nul af en volg de regels nu strikt."
)

_VERBODEN_FORMEEL = re.compile(r"\b(u|uw)\b", re.IGNORECASE)
_OPT_OUT_PATTERN = re.compile(
    r"(opt-?out|afmelden|geen interesse|niet\s+(?:meer\s+)?(?:wil(?:t)?\s+)?ontvangen|laat[^.!?]{0,30}weten)",
    re.IGNORECASE,
)
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


class ChatLLM(Protocol):
    async def ainvoke(self, messages: list) -> object: ...


def _build_llm() -> ChatLLM:
    return ChatAnthropic(model=ANTHROPIC_MODEL, api_key=ANTHROPIC_API_KEY, temperature=0.6)


def _validate_mail(mail: str) -> list[str]:
    problemen: list[str] = []
    zinnen = [z for z in re.split(r"(?<=[.!?])\s+", mail.strip()) if z]
    if len(zinnen) > 4:
        problemen.append(f"te veel zinnen ({len(zinnen)} > 4)")
    if _VERBODEN_FORMEEL.search(mail):
        problemen.append("gebruikt 'u'/'uw' in plaats van 'je/jullie'")
    if "omgeving van" in mail.lower():
        problemen.append("gebruikt verboden uitdrukking 'omgeving van'")
    if not _OPT_OUT_PATTERN.search(mail):
        problemen.append("geen opt-out-zin aanwezig")
    if _SPAM_TRIGGER_PATTERN.search(mail):
        problemen.append("gebruikt een klassiek spam-triggerwoord (bv. 'gratis', 'klik hier')")
    if mail.count("!") > 1:
        problemen.append("te veel uitroeptekens (max 1)")
    if _MOOIE_WEBSITE_PATTERN.search(mail):
        problemen.append("gebruikt verboden uitdrukking 'mooie website'")
    if len(_AUTOMATISERING_PATTERN.findall(mail)) > 1:
        problemen.append("gebruikt 'automatisering' meer dan één keer")
    return problemen


def _validate_onderwerpregel(onderwerp: str) -> list[str]:
    problemen: list[str] = []
    if not onderwerp:
        problemen.append("geen onderwerpregel gegenereerd")
        return problemen
    woorden = onderwerp.split()
    if len(woorden) > 6:
        problemen.append(f"onderwerpregel te lang ({len(woorden)} > 6 woorden)")
    return problemen


def _parse_response(raw: str) -> tuple[str, str]:
    """Splitst het ONDERWERP/MAIL-formaat; valt terug op de volledige tekst als mail
    wanneer het model het formaat niet volgde (mag de run nooit laten crashen)."""
    match = _RESPONSE_PATTERN.search(raw)
    if not match:
        return "", raw.strip()
    return match.group("onderwerp").strip(), match.group("mail").strip()


async def _generate(
    llm: ChatLLM, lead: LeadState, extra_instructie: str | None = None
) -> tuple[str, str]:
    haakje = lead["haakje"] or lead["ijsbreker"]
    content = f"Bedrijf: {lead['bedrijfsnaam']}, sector: {lead['niche']}.\n" f"Gevonden handmatig proces: {haakje}\n"
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
            lead["mail"] = mail
            lead["onderwerpregel"] = onderwerp
        except Exception as exc:  # noqa: BLE001 - een kapotte mail-generatie mag de run niet stoppen
            lead["status"] = "error"
            lead["error_reden"] = f"Mail-generatie-fout: {exc}"
            logger.warning("Mail-generatie-fout voor %s: %s", lead["bedrijfsnaam"], exc)

    return leads
