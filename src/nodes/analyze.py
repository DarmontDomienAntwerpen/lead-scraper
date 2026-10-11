"""Analyze node: determine the niche, reject bad data, write the icebreaker.

One Claude call per lead that reasons through four internal roles (ANALYST ->
REVIEWER -> STRATEGIST -> COPYWRITER, see SYSTEM_PROMPT) and returns structured
output (`AnalysisResult`). Bad-data leads (cookie wall, empty/error page, no real
company information) are rejected here, so no sales email is written about a
useless scrape.

The prompt is intentionally in Dutch: the output is a Flemish sales email.
"""

from __future__ import annotations

import logging
from typing import Protocol

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_anthropic import ChatAnthropic
from pydantic import BaseModel, Field

from src.config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL
from src.state import LeadState

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Je bent een team van vier rollen die ÉÉN antwoord opleveren over een \
Belgisch zelfstandige/KMO op basis van ruwe tekst van hun website. Doorloop deze vier \
stappen intern, in deze volgorde, en geef pas op het einde het structured-output resultaat:

1. ANALIST: bepaal de EXACTE niche van het bedrijf. Wees specifiek en concreet \
   (bv. "hondenuitlaatdienst", "fietsenmaker gespecialiseerd in elektrische fietsen", \
   "boekhouder voor zelfstandigen in de bouwsector"). Gebruik NOOIT vage termen zoals \
   "dienstverlener" of "bedrijf".

2. REVIEWER: controleer of de tekst echte, bruikbare bedrijfsinformatie bevat. Als de \
   tekst vooral bestaat uit een cookie-muur/consent-banner, een lege of kapotte pagina, \
   een "onder constructie"-melding, of geen enkel concreet detail over het bedrijf zelf \
   geeft, markeer dit dan als ongeldig (is_valid=False) met een korte reject_reason. \
   Schrijf in dat geval GEEN niche of icebreaker (laat ze leeg).

3. STRATEEG: als de data wel geldig is, kies één concreet, specifiek detail van de \
   website (een dienst, een unieke aanpak, een vermelding van jaren ervaring, een \
   specialisatie, een merknaam, ...) dat een goede aanleiding vormt voor een ijsbreker. \
   Vermijd generieke details die op elke website zouden passen.

4. COPYWRITER: schrijf op basis van dat detail één natuurlijke, vlotte Nederlandse zin \
   (Vlaams, "je/jullie", nooit "u") die als ijsbreker kan dienen voor een verkoopmail. \
   Geen overdreven enthousiasme, geen uitroeptekens.

Geef enkel het structured-output resultaat terug, geen extra uitleg."""


class AnalysisResult(BaseModel):
    """Structured output of the analysis step."""

    niche: str = Field(default="", description="Exact, specific niche of the business.")
    icebreaker: str = Field(default="", description="One-sentence Dutch icebreaker based on a concrete detail from the website.")
    is_valid: bool = Field(description="False for bad data: cookie wall, empty/error page, no company information.")
    reject_reason: str | None = Field(default=None, description="Reason for rejection when is_valid is False.")


class StructuredLLM(Protocol):
    async def ainvoke(self, messages: list) -> AnalysisResult: ...


def _build_llm() -> StructuredLLM:
    base = ChatAnthropic(model=ANTHROPIC_MODEL, api_key=ANTHROPIC_API_KEY, temperature=0.3)
    return base.with_structured_output(AnalysisResult)


async def analyze_leads(leads: list[LeadState], llm: StructuredLLM | None = None) -> list[LeadState]:
    """Analyse every pending lead with scraped text; mutates the leads in place."""
    active_llm = llm if llm is not None else _build_llm()

    for lead in leads:
        if lead["status"] != "pending":
            continue
        if not lead["raw_text"]:
            lead["status"] = "error"
            lead["error_reason"] = "No scraped text available for analysis"
            continue

        try:
            outcome = await active_llm.ainvoke(
                [
                    SystemMessage(content=SYSTEM_PROMPT),
                    HumanMessage(
                        content=(
                            f"Bedrijfsnaam: {lead['company_name']}\n"
                            f"Website: {lead['website']}\n\n"
                            f"Ruwe websitetekst:\n{lead['raw_text']}"
                        )
                    ),
                ]
            )
        except Exception as exc:  # noqa: BLE001 - one failed analysis must not stop the run
            lead["status"] = "error"
            lead["error_reason"] = f"Analysis error: {exc}"
            logger.warning("Analysis error for %s: %s", lead["company_name"], exc)
            continue

        if not outcome.is_valid:
            lead["status"] = "skipped"
            lead["error_reason"] = outcome.reject_reason or "Bad data (rejected in review step)"
            logger.info("Lead rejected in review step: %s (%s)", lead["company_name"], lead["error_reason"])
            continue

        lead["niche"] = outcome.niche
        lead["icebreaker"] = outcome.icebreaker
        lead["status"] = "ok"

    return leads
