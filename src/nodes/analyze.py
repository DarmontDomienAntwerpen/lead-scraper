"""REVIEWER: analyze-node — niche bepalen, bad-data weigeren, ijsbreker schrijven.

Eén Claude-call per lead die intern vier denkstappen doorloopt (ANALIST ->
REVIEWER -> STRATEEG -> COPYWRITER, zie SYSTEM_PROMPT) en die als
structured output (`AnalyseResultaat`) teruggeeft. Bad-data leads
(cookie-muur, lege/error-pagina, geen echte bedrijfsinfo) worden hier al
geweigerd zodat we geen verkoopmail schrijven over een waardeloze scrape.
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
   geeft, markeer dit dan als ongeldig (is_valid=False) met een korte reject_reden. \
   Schrijf in dat geval GEEN niche of ijsbreker (laat ze leeg).

3. STRATEEG: als de data wel geldig is, kies één concreet, specifiek detail van de \
   website (een dienst, een unieke aanpak, een vermelding van jaren ervaring, een \
   specialisatie, een merknaam, ...) dat een goede aanleiding vormt voor een ijsbreker. \
   Vermijd generieke details die op elke website zouden passen.

4. COPYWRITER: schrijf op basis van dat detail één natuurlijke, vlotte Nederlandse zin \
   (Vlaams, "je/jullie", nooit "u") die als ijsbreker kan dienen voor een verkoopmail. \
   Geen overdreven enthousiasme, geen uitroeptekens.

Geef enkel het structured-output resultaat terug, geen extra uitleg."""


class AnalyseResultaat(BaseModel):
    """Structured output van de REVIEWER-stap."""

    niche: str = Field(default="", description="Exacte, specifieke niche van het bedrijf.")
    ijsbreker: str = Field(default="", description="Eén zin ijsbreker gebaseerd op een concreet sitedetail.")
    is_valid: bool = Field(description="False bij bad data: cookie-muur, lege/error-pagina, geen bedrijfsinfo.")
    reject_reden: str | None = Field(default=None, description="Reden voor afwijzing indien is_valid False is.")


class StructuredLLM(Protocol):
    async def ainvoke(self, messages: list) -> AnalyseResultaat: ...


def _build_llm() -> StructuredLLM:
    base = ChatAnthropic(model=ANTHROPIC_MODEL, api_key=ANTHROPIC_API_KEY, temperature=0.3)
    return base.with_structured_output(AnalyseResultaat)


async def analyze_leads(leads: list[LeadState], llm: StructuredLLM | None = None) -> list[LeadState]:
    """Analyseert elke pending lead met geldige scrape-tekst; muteert de leads in-place."""
    active_llm = llm if llm is not None else _build_llm()

    for lead in leads:
        if lead["status"] != "pending":
            continue
        if not lead["ruwe_tekst"]:
            lead["status"] = "error"
            lead["error_reden"] = "Geen scrape-tekst beschikbaar voor analyse"
            continue

        try:
            resultaat = await active_llm.ainvoke(
                [
                    SystemMessage(content=SYSTEM_PROMPT),
                    HumanMessage(
                        content=(
                            f"Bedrijfsnaam: {lead['bedrijfsnaam']}\n"
                            f"Website: {lead['website']}\n\n"
                            f"Ruwe websitetekst:\n{lead['ruwe_tekst']}"
                        )
                    ),
                ]
            )
        except Exception as exc:  # noqa: BLE001 - een kapotte analyse mag de run niet stoppen
            lead["status"] = "error"
            lead["error_reden"] = f"Analyse-fout: {exc}"
            logger.warning("Analyse-fout voor %s: %s", lead["bedrijfsnaam"], exc)
            continue

        if not resultaat.is_valid:
            lead["status"] = "skipped"
            lead["error_reden"] = resultaat.reject_reden or "Bad data (REVIEWER-stap)"
            logger.info("Lead afgewezen door REVIEWER: %s (%s)", lead["bedrijfsnaam"], lead["error_reden"])
            continue

        lead["niche"] = resultaat.niche
        lead["ijsbreker"] = resultaat.ijsbreker
        lead["status"] = "ok"

    return leads
