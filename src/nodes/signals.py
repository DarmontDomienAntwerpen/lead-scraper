"""Signals node: rule-based detection of manual processes.

Runs between scrape and analyze, on the already fetched `raw_html` (no new requests).
It deliberately uses the unstripped HTML instead of `raw_text`: structural signals such
as <form> elements, mailto:/tel: links and widget scripts (Calendly, Intercom, ...) do
not survive the tag stripping in scrape.py.

Extensible per sector: `SECTOR_KEYWORD_OVERRIDES` is empty by default and can add extra
keywords per signal for a given niche (lowercase, as returned by analyze.py) without
touching the base rules (`_KEYWORDS`).

The need score and the hook are computed here, before the expensive LLM steps, so
`apply_signals` can mark leads below the threshold as "skipped" right away. That saves
analyze and write calls for leads that would not go through anyway.

Keywords and hooks are in Dutch (and some French/English) because the target websites
are Belgian.
"""

from __future__ import annotations

import logging
import re

from src.config import MIN_NEED_SCORE
from src.state import LeadState

logger = logging.getLogger(__name__)

# Base rules: per signal, keywords searched for in the lowercased HTML.
_KEYWORDS: dict[str, list[str]] = {
    "has_booking_tool": [
        # Generic tools
        "calendly", "acuityscheduling", "acuity", "simplybook", "reservio",
        "planity", "setmore", "booksy", "jane app", "appointy", "shore.us",
        "squareup.com", "booker", "genbook", "phorest", "timely", "shortcuts",
        "mindbody", "glofox", "zenoti",
        # Salon/beauty/wellness
        "fresha", "treatwell", "salonkee", "vagaro", "koifish",
        # Medical / physiotherapy
        "doctoranytime", "doctena", "intramed", "phy.so", "liantis",
        # Generic CTAs: online booking exists even when the tool is loaded externally
        "online boeken", "boek nu", "book now", "maak een afspraak online",
        "online reserveren", "reserveer online", "afspraak online",
        "book online", "boek online", "plan je afspraak", "plan een afspraak",
        "afspraak plannen", "afspraakonline", "online boeking", "online booking",
        "afspraak inplannen", "maak hier een afspraak", "afspraak via onze website",
    ],
    "has_webshop": [
        "shopify", "woocommerce", "winkelwagen", "add to cart",
        "in winkelwagen", "checkout",
    ],
    "has_livechat": ["intercom", "crisp.chat", "tawk.to", "drift", "tidio", "freshchat", "hubspot"],
    "has_email_marketing": [
        "mailchimp", "klaviyo",
        "schrijf je in voor onze nieuwsbrief", "inschrijven nieuwsbrief",
    ],
    "quote_via_mail": [
        "offerte aanvragen", "stuur ons een mail",
        "vraag een offerte", "offerte per mail",
    ],
    # Large FAQ or knowledge base -> opportunity for a RAG chatbot
    "has_faq": [
        "veelgestelde vragen", "faq", "frequently asked questions",
        "veel gestelde vragen", "kennisbank", "helpcentrum", "help center",
    ],
    # Auto-reply keywords: absent while a form exists means follow-up is manual
    "has_autoresponse": [
        "automatisch antwoord", "auto-reply", "bevestigingsmail", "je ontvangt een bevestiging",
        "we nemen zo snel mogelijk contact op", "we contacteren je",
    ],
    # Knowledge-intensive sector -> RAG opportunity (documents, case files, legislation)
    "knowledge_sector": [
        "advocaat", "advocatenkantoor", "juridisch advies", "notaris", "notariaat",
        "verzekeringsmakelaar", "verzekeringsagent", "vastgoedkantoor", "immokantoor",
        "accountant", "fiscalist", "belastingadvies", "sociale secretariaat",
        "hr-kantoor", "personeelsdienst", "compliance", "juridische dienst",
    ],
    "has_phone_complaint": [
        # Dutch
        "voicemail", "niet opgenomen", "niet bereikbaar", "moeilijk bereikbaar",
        "geen antwoord", "antwoordt niet", "lang wachten", "niet teruggebeld",
        "bereikbaarheid", "niet te bereiken",
        # French
        "messagerie", "injoignable", "pas de réponse",
        # English
        "no answer", "unreachable", "couldn't reach", "went to voicemail",
    ],
}

# Add extra keywords per niche (lowercase) here, e.g.:
# SECTOR_KEYWORD_OVERRIDES["fietsenmaker"] = {"has_booking_tool": ["herstelafspraak"]}
SECTOR_KEYWORD_OVERRIDES: dict[str, dict[str, list[str]]] = {}

_PHONE_PATTERN = re.compile(r"(\+32\s?\d(?:[\s.-]?\d){7,8}|\b0\d(?:[\s.-]?\d){7,8}\b)")

# More manual signals and fewer tools -> higher score.
_MANUAL_SIGNAL_WEIGHTS = {
    "has_phone_complaint": 3,  # strongest signal: customers complain about reachability
    "quote_via_mail": 2,
    "phone_only_cta": 2,       # phone number present, no booking tool
    "knowledge_sector": 2,     # knowledge-intensive sector -> RAG opportunity
    "has_faq": 1,              # large FAQ -> chatbot/RAG opportunity
    "mailto_contact": 1,
    # form_no_autoresponse does not add to the score (too weak on its own)
    # but can still be used as the hook via select_hook()
}
_TOOL_SIGNAL_PENALTIES = {
    # The pitch covers more than phone handling (email, chatbot, RAG), so a booking tool
    # only gets a small penalty: it doesn't rule out those other opportunities.
    "has_booking_tool": 1,
    "has_webshop": 2,
    "has_livechat": 1,
    "has_email_marketing": 1,
}


def _keywords_for(signal: str, niche: str | None) -> list[str]:
    base = list(_KEYWORDS.get(signal, []))
    override = SECTOR_KEYWORD_OVERRIDES.get((niche or "").lower(), {})
    return base + override.get(signal, [])


def _contains_any(haystack: str, keywords: list[str]) -> bool:
    return any(keyword in haystack for keyword in keywords)


def detect_signals(html: str, niche: str | None = None) -> dict[str, bool]:
    """Compute the signal booleans from already fetched HTML; makes no requests."""
    lower = html.lower()

    has_form = "<form" in lower
    mailto_contact = "mailto:" in lower
    has_booking_tool = _contains_any(lower, _keywords_for("has_booking_tool", niche))
    has_webshop = _contains_any(lower, _keywords_for("has_webshop", niche))
    has_livechat = _contains_any(lower, _keywords_for("has_livechat", niche))
    has_email_marketing = _contains_any(lower, _keywords_for("has_email_marketing", niche))
    quote_via_mail = _contains_any(lower, _keywords_for("quote_via_mail", niche)) and not has_form
    has_phone = "tel:" in lower or bool(_PHONE_PATTERN.search(html))
    phone_only_cta = has_phone and not has_booking_tool
    has_faq = _contains_any(lower, _keywords_for("has_faq", niche))
    knowledge_sector = _contains_any(lower, _keywords_for("knowledge_sector", niche))
    # form present but no auto-reply wording -> follow-up is manual
    has_autoresponse = _contains_any(lower, _keywords_for("has_autoresponse", niche))
    form_no_autoresponse = has_form and not has_autoresponse and not has_booking_tool

    return {
        "mailto_contact": mailto_contact,
        "has_form": has_form,
        "has_booking_tool": has_booking_tool,
        "has_webshop": has_webshop,
        "has_livechat": has_livechat,
        "has_email_marketing": has_email_marketing,
        "phone_only_cta": phone_only_cta,
        "quote_via_mail": quote_via_mail,
        "has_faq": has_faq,
        "knowledge_sector": knowledge_sector,
        "form_no_autoresponse": form_no_autoresponse,
        "has_phone_complaint": False,  # set from reviews in apply_signals
    }


def compute_score(signal_flags: dict[str, bool]) -> int:
    score = sum(weight for key, weight in _MANUAL_SIGNAL_WEIGHTS.items() if signal_flags.get(key))
    score -= sum(penalty for key, penalty in _TOOL_SIGNAL_PENALTIES.items() if signal_flags.get(key))
    return max(score, 0)


def select_hook(signal_flags: dict[str, bool]) -> str:
    """Pick one concrete hook (in Dutch, used in the email prompt): the most notable manual flow."""
    if signal_flags.get("has_phone_complaint"):
        return "Klanten vermelden in reviews dat ze moeilijk telefonisch contact krijgen of op voicemail stuiten."
    if signal_flags.get("knowledge_sector"):
        return "Kennisintensieve sector: klanten stellen waarschijnlijk steeds dezelfde vragen die handmatig beantwoord worden of waarbij dossiers/documenten handmatig opgezocht worden."
    if signal_flags.get("quote_via_mail"):
        return "Offertes worden via mail aangevraagd en wellicht handmatig verwerkt."
    if signal_flags.get("phone_only_cta"):
        return "Afspraken lopen telefonisch — geen online boekingstool gevonden op de site."
    if signal_flags.get("form_no_autoresponse"):
        return "Contactformulier aanwezig maar geen automatische bevestiging of opvolging gedetecteerd — leads worden waarschijnlijk handmatig opgevolgd."
    if signal_flags.get("has_faq"):
        return "Grote FAQ-sectie aanwezig — wijst op veel herhalende klantvragen die een chatbot of kennissysteem automatisch kan afhandelen."
    if signal_flags.get("mailto_contact") and not signal_flags.get("has_form"):
        return "Intake loopt via e-mail (mailto) in plaats van een gekoppeld formulier."
    return ""


def apply_signals(leads: list[LeadState], min_score: int = MIN_NEED_SCORE) -> list[LeadState]:
    """Fill signals, score and hook for every pending lead and filter out leads below the
    threshold before analyze/write; mutates the leads in place."""
    for lead in leads:
        if lead["status"] != "pending":
            continue

        html = lead.get("raw_html") or lead["raw_text"]
        if not html:
            continue

        signal_flags = detect_signals(html, lead.get("niche"))

        # Scan reviews for complaints about phone reachability (no extra request).
        reviews = lead.get("reviews_text", "")
        if reviews and _contains_any(reviews.lower(), _keywords_for("has_phone_complaint", None)):
            signal_flags["has_phone_complaint"] = True

        score = compute_score(signal_flags)
        hook = select_hook(signal_flags)

        lead["signal_flags"] = signal_flags
        lead["need_score"] = score
        lead["hook"] = hook

        if score < min_score:
            lead["status"] = "skipped"
            lead["error_reason"] = f"Need score {score} below threshold {min_score}"
            logger.info(
                "Lead skipped before analyze/write (score %d < %d): %s",
                score,
                min_score,
                lead["company_name"],
            )

    return leads
