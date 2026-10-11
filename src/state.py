"""Defines the LangGraph state that flows between all nodes.

Design choice: the graph works on the whole batch (one list of leads in the
state), not one lead per graph invocation. Every node (search/scrape/analyze/
write/export) iterates over `leads` internally and catches errors per lead
(try/except -> status="error"). One broken lead never crashes the run; the other
leads simply continue. Conditional edges in graph.py route at batch level
("are there still active leads left to scrape/write?").
"""

from __future__ import annotations

from typing import Literal, TypedDict

LeadStatus = Literal["pending", "ok", "skipped", "error"]


class LeadState(TypedDict):
    """All data collected for a single lead as it moves through the pipeline."""

    search_term: str
    place_id: str
    company_name: str
    website: str | None
    phone: str | None
    email: str | None
    email_status: str | None  # NeverBounce result: valid/invalid/disposable/catchall/unknown/None
    contact_url: str | None  # contact page URL, only set when no email address was found
    raw_text: str
    raw_html: str  # unstripped HTML from scrape.py, used for rule-based signal detection
    reviews_text: str  # combined Google Reviews text, fetched in the search step
    signal_flags: dict[str, bool]  # booleans from src/nodes/signals.py (mailto_contact, has_form, ...)
    need_score: int  # higher = more manual processes and fewer tools
    hook: str  # concrete (Dutch) pain point for the pitch, e.g. "intake loopt via mail"
    niche: str
    icebreaker: str
    subject_line: str  # max 6 words, generated together with the email
    mail: str
    status: LeadStatus
    error_reason: str | None
    source: str
    fetched_at: str  # ISO timestamp of the search


def new_lead(*, search_term: str, place_id: str, company_name: str,
             website: str | None, phone: str | None, source: str,
             fetched_at: str) -> LeadState:
    """Factory for a fresh LeadState with empty/default fields."""
    return LeadState(
        search_term=search_term,
        place_id=place_id,
        company_name=company_name,
        website=website,
        phone=phone,
        email=None,
        email_status=None,
        contact_url=None,
        raw_text="",
        raw_html="",
        reviews_text="",
        signal_flags={},
        need_score=0,
        hook="",
        niche="",
        icebreaker="",
        subject_line="",
        mail="",
        status="pending",
        error_reason=None,
        source=source,
        fetched_at=fetched_at,
    )


class PipelineState(TypedDict):
    """Top-level graph state: run parameters plus the list of leads each node updates."""

    search_term: str
    region: str
    max_leads: int
    leads: list[LeadState]
    output_path: str | None
    min_need_score: int  # threshold for the signals node; graph.py falls back to config.MIN_NEED_SCORE
