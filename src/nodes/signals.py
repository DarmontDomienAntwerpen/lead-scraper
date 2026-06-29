"""ARCHITECT: signals-node — regelgebaseerde detectie van handmatige processen.

Draait tussen scrape en analyze, op de al-opgehaalde `ruwe_html` (geen nieuwe requests).
We gebruiken bewust de ongestripte HTML i.p.v. `ruwe_tekst`: structurele signalen zoals
<form>-elementen, mailto:/tel:-links en widget-scripts (Calendly, Intercom, ...) overleven
het tag-strippen in scrape.py niet.

Modulair per sector: `SECTOR_KEYWORD_OVERRIDES` is leeg bij default en kan per niche
(kleine letters, zoals analyze.py die teruggeeft) aangevuld worden met extra keywords per
signaal, zonder de basisregels (`_KEYWORDS`) aan te raken.

De behoefte-score en het haakje worden hier al bepaald (vóór de dure LLM-stappen) zodat
`apply_signals` leads onder de drempel meteen als "skipped" kan markeren - dat scheelt
analyze- en write-calls voor leads die toch niet doorstromen.
"""

from __future__ import annotations

import logging
import re

from src.config import MIN_BEHOEFTE_SCORE
from src.state import LeadState

logger = logging.getLogger(__name__)

# Basisregels: per signaal een lijst keywords die op de lowercased HTML gezocht worden.
_KEYWORDS: dict[str, list[str]] = {
    "has_booking_tool": [
        "calendly",
        "acuityscheduling",
        "acuity",
        "afspraak maken",
        "online boeken",
        "reserveer",
        "book now",
        "boek nu",
    ],
    "has_webshop": [
        "shopify",
        "woocommerce",
        "winkelwagen",
        "add to cart",
        "in winkelwagen",
        "checkout",
    ],
    "has_livechat": ["intercom", "crisp.chat", "tawk.to", "drift"],
    "has_email_marketing": [
        "mailchimp",
        "klaviyo",
        "schrijf je in voor onze nieuwsbrief",
        "inschrijven nieuwsbrief",
    ],
    "offerte_via_mail": [
        "offerte aanvragen",
        "stuur ons een mail",
        "vraag een offerte",
        "offerte per mail",
    ],
}

# Vul hier per niche (lowercase) extra/overschrijvende keywords aan, bv.:
# SECTOR_KEYWORD_OVERRIDES["fietsenmaker"] = {"has_booking_tool": ["herstelafspraak"]}
SECTOR_KEYWORD_OVERRIDES: dict[str, dict[str, list[str]]] = {}

_PHONE_PATTERN = re.compile(r"(\+32\s?\d(?:[\s.-]?\d){7,8}|\b0\d(?:[\s.-]?\d){7,8}\b)")

# Hoe meer handmatige signalen en hoe minder tools aanwezig, hoe hoger de score.
_MANUAL_SIGNAL_WEIGHTS = {"offerte_via_mail": 2, "phone_only_cta": 2, "mailto_contact": 1}
_TOOL_SIGNAL_PENALTIES = {
    "has_form": 1,
    "has_booking_tool": 2,
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
    """Bepaalt de signaal-booleans op reeds-opgehaalde HTML; doet geen requests."""
    lower = html.lower()

    has_form = "<form" in lower
    mailto_contact = "mailto:" in lower
    has_booking_tool = _contains_any(lower, _keywords_for("has_booking_tool", niche))
    has_webshop = _contains_any(lower, _keywords_for("has_webshop", niche))
    has_livechat = _contains_any(lower, _keywords_for("has_livechat", niche))
    has_email_marketing = _contains_any(lower, _keywords_for("has_email_marketing", niche))
    offerte_via_mail = _contains_any(lower, _keywords_for("offerte_via_mail", niche)) and not has_form
    has_phone = "tel:" in lower or bool(_PHONE_PATTERN.search(html))
    phone_only_cta = has_phone and not has_booking_tool and not has_form

    return {
        "mailto_contact": mailto_contact,
        "has_form": has_form,
        "has_booking_tool": has_booking_tool,
        "has_webshop": has_webshop,
        "has_livechat": has_livechat,
        "has_email_marketing": has_email_marketing,
        "phone_only_cta": phone_only_cta,
        "offerte_via_mail": offerte_via_mail,
    }


def compute_score(signalen: dict[str, bool]) -> int:
    score = sum(weight for key, weight in _MANUAL_SIGNAL_WEIGHTS.items() if signalen.get(key))
    score -= sum(penalty for key, penalty in _TOOL_SIGNAL_PENALTIES.items() if signalen.get(key))
    return max(score, 0)


def select_haakje(signalen: dict[str, bool]) -> str:
    """Kiest één concreet Nederlands haakje - de meest opvallende handmatige flow."""
    if signalen.get("offerte_via_mail"):
        return "Offertes worden via mail aangevraagd en wellicht handmatig opgemaakt."
    if signalen.get("phone_only_cta"):
        return "Geen online boeking of formulier - afspraken gaan blijkbaar telefonisch."
    if signalen.get("mailto_contact") and not signalen.get("has_form"):
        return "Intake loopt via e-mail (mailto) in plaats van een gekoppeld formulier."
    return ""


def apply_signals(leads: list[LeadState], min_score: int = MIN_BEHOEFTE_SCORE) -> list[LeadState]:
    """Vult signalen/score/haakje voor elke pending lead en filtert onder de drempel uit
    vóór analyze/write; muteert de leads in-place."""
    for lead in leads:
        if lead["status"] != "pending":
            continue

        html = lead.get("ruwe_html") or lead["ruwe_tekst"]
        if not html:
            continue

        signalen = detect_signals(html, lead.get("niche"))
        score = compute_score(signalen)
        haakje = select_haakje(signalen)

        lead["signalen"] = signalen
        lead["behoefte_score"] = score
        lead["haakje"] = haakje

        if score < min_score:
            lead["status"] = "skipped"
            lead["error_reden"] = f"Behoefte-score {score} onder drempel {min_score}"
            logger.info(
                "Lead overgeslagen vóór analyze/write (score %d < %d): %s",
                score,
                min_score,
                lead["bedrijfsnaam"],
            )

    return leads
