"""ARCHITECT: definieert de LangGraph State die tussen alle nodes stroomt.

Ontwerpkeuze: de graph werkt op het niveau van de volledige batch (één lijst
van leads in de state), niet één lead per graph-invocatie. Elke node
(search/scrape/analyze/write/export) itereert intern over `leads` en vangt
fouten per lead af (try/except -> status="error"). Zo crasht één kapotte
lead nooit de hele run, en blijven de overige leads gewoon doorlopen.
Conditionele edges in graph.py routeren op batch-niveau (bv. "zijn er nog
geldige leads over om te scraping/schrijven?").
"""

from __future__ import annotations

from typing import Literal, TypedDict

LeadStatus = Literal["pending", "ok", "skipped", "error"]


class LeadState(TypedDict):
    """Alle data die voor één lead doorheen de pipeline wordt opgebouwd."""

    zoekterm: str
    place_id: str
    bedrijfsnaam: str
    website: str | None
    telefoon: str | None
    email: str | None
    ruwe_tekst: str
    niche: str
    ijsbreker: str
    mail: str
    status: LeadStatus
    error_reden: str | None
    bron: str
    opgehaald_op: str  # ISO-timestamp van het search-moment


def new_lead(*, zoekterm: str, place_id: str, bedrijfsnaam: str,
             website: str | None, telefoon: str | None, bron: str,
             opgehaald_op: str) -> LeadState:
    """Fabriek voor een verse LeadState met lege/standaard velden."""
    return LeadState(
        zoekterm=zoekterm,
        place_id=place_id,
        bedrijfsnaam=bedrijfsnaam,
        website=website,
        telefoon=telefoon,
        email=None,
        ruwe_tekst="",
        niche="",
        ijsbreker="",
        mail="",
        status="pending",
        error_reden=None,
        bron=bron,
        opgehaald_op=opgehaald_op,
    )


class PipelineState(TypedDict):
    """De top-level graph state: run-parameters + de lijst leads die groeit/verandert per node."""

    zoekterm: str
    regio: str
    max_leads: int
    leads: list[LeadState]
    output_path: str | None
