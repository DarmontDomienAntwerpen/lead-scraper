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

STRIKTE REGELS (niet onderhandelbaar):
- Taal: natuurlijk en vlot Vlaams. Spreek de lezer aan met "je"/"jullie". Gebruik NOOIT \
  "u", "uw" of de uitdrukking "omgeving van".
- Lengte: maximaal 4 zinnen in totaal, geen titel, geen aanhef ("Beste ..."), geen \
  afsluiter ("Met vriendelijke groeten").
- Open met (een lichte herformulering van) de gegeven ijsbreker.
- Leg in 1-2 zinnen concreet uit hoe automatisering specifiek voor hún niche tijd kan \
  besparen - benoem iets tastbaars (bv. offertes opmaken, mails beantwoorden, planning, \
  facturatie). Noem GEEN concreet aantal uren of percentage besparing - dat verschilt te \
  veel per bedrijf om eerlijk te kunnen beweren; blijf bij de taak zelf, niet bij een cijfer.
- Eindig met een laagdrempelige call-to-action: vraag of je een kort, vrijblijvend \
  gesprek mag inplannen om te horen welke concrete taken of problemen bij hen veel tijd \
  kosten. Beloof GEEN kant-en-klare demo of oplossing - het gesprek is net om hun \
  situatie te leren kennen, niet om iets te tonen dat al af is.
- Voeg een korte, duidelijke opt-out-zin toe (verplicht, bv. "Laat het gerust weten als \
  je dit niet wil ontvangen.").
- Geen overdreven salesy taal, geen uitroeptekens-spam.

Geef ENKEL de platte mailtekst terug, niets anders."""

REGENERATE_INSTRUCTION = (
    "Je vorige poging voldeed niet aan de regels ({problemen}). Herschrijf de mail van "
    "nul af en volg de regels nu strikt."
)

_VERBODEN_FORMEEL = re.compile(r"\b(u|uw)\b", re.IGNORECASE)
_OPT_OUT_PATTERN = re.compile(
    r"(opt-?out|afmelden|geen interesse|niet\s+(?:meer\s+)?(?:wil(?:t)?\s+)?ontvangen|laat[^.!?]{0,30}weten)",
    re.IGNORECASE,
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
    return problemen


async def _generate(llm: ChatLLM, lead: LeadState, extra_instructie: str | None = None) -> str:
    content = (
        f"Bedrijfsnaam: {lead['bedrijfsnaam']}\n"
        f"Niche: {lead['niche']}\n"
        f"Ijsbreker: {lead['ijsbreker']}\n"
    )
    if extra_instructie:
        content += f"\n{extra_instructie}\n"
    response = await llm.ainvoke(
        [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=content)]
    )
    return str(response.content).strip()


async def write_mails(leads: list[LeadState], llm: ChatLLM | None = None) -> list[LeadState]:
    """Schrijft de mail voor elke lead met status 'ok'; muteert de leads in-place."""
    active_llm = llm if llm is not None else _build_llm()

    for lead in leads:
        if lead["status"] != "ok":
            continue
        try:
            mail = await _generate(active_llm, lead)
            problemen = _validate_mail(mail)
            if problemen:
                logger.info(
                    "Mail voor %s voldoet niet aan de regels (%s) - hergenereren",
                    lead["bedrijfsnaam"],
                    "; ".join(problemen),
                )
                mail = await _generate(
                    active_llm,
                    lead,
                    REGENERATE_INSTRUCTION.format(problemen="; ".join(problemen)),
                )
                problemen = _validate_mail(mail)
                if problemen:
                    logger.warning(
                        "Mail voor %s voldoet ook na hergeneratie niet (%s) - manuele "
                        "review extra belangrijk hier",
                        lead["bedrijfsnaam"],
                        "; ".join(problemen),
                    )
                    lead["error_reden"] = "Mail-validatie: " + "; ".join(problemen)
            lead["mail"] = mail
        except Exception as exc:  # noqa: BLE001 - een kapotte mail-generatie mag de run niet stoppen
            lead["status"] = "error"
            lead["error_reden"] = f"Mail-generatie-fout: {exc}"
            logger.warning("Mail-generatie-fout voor %s: %s", lead["bedrijfsnaam"], exc)

    return leads
