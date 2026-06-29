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
    email_status: str | None  # NeverBounce-resultaat: valid/invalid/disposable/catchall/unknown/None
    contact_url: str | None  # contactpagina-URL, enkel gezet als er geen e-mailadres gevonden werd
    ruwe_tekst: str
    ruwe_html: str  # ongestripte HTML zoals scrape.py die al ophaalt, voor regelgebaseerde signaaldetectie
    signalen: dict[str, bool]  # booleans uit src/nodes/signals.py (mailto_contact, has_form, ...)
    behoefte_score: int  # hoe meer handmatige signalen/hoe minder tools, hoe hoger
    haakje: str  # concreet Nederlands signaal-haakje voor de pitch, bv. "intake loopt via mail"
    niche: str
    ijsbreker: str
    onderwerpregel: str  # max 6 woorden, gegenereerd samen met de mail
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
        email_status=None,
        contact_url=None,
        ruwe_tekst="",
        ruwe_html="",
        signalen={},
        behoefte_score=0,
        haakje="",
        niche="",
        ijsbreker="",
        onderwerpregel="",
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
    min_behoefte_score: int  # drempel voor de signals-node; ontbreekt hij, dan valt graph.py terug op config.MIN_BEHOEFTE_SCORE
