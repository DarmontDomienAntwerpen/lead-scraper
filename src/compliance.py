"""Compliance: suppression list and source traceability.

Two responsibilities:
1. `load_suppression_domains` / `apply_suppression`: domains on the suppression
   list are never exported, however well the rest of the pipeline scored them.
2. Every `LeadState` already carries `source`, `fetched_at` and `search_term`
   (see state.py), so export.py reuses them for the audit-log sheet; no
   separate log structure is needed.
"""

from __future__ import annotations

import csv
import logging
import os
from urllib.parse import urlparse

from src.config import SUPPRESSION_CSV
from src.state import LeadState

logger = logging.getLogger(__name__)

# Public email providers must never be suppressed as a company domain: one freelancer
# with a gmail address would otherwise block every future gmail lead.
FREEMAIL_DOMAINS = {
    "gmail.com", "hotmail.com", "outlook.com", "live.com", "yahoo.com",
    "icloud.com", "telenet.be", "skynet.be", "proximus.be",
}


def is_suppressible_domain(domain: str) -> bool:
    """False for public email providers: they never belong on the suppression list."""
    return domain.lower() not in FREEMAIL_DOMAINS


def _domain(url: str | None) -> str | None:
    if not url:
        return None
    netloc = urlparse(url if "://" in url else f"https://{url}").netloc.lower()
    return netloc.removeprefix("www.") or None


def load_suppression_domains(path: str = SUPPRESSION_CSV) -> set[str]:
    """Read suppression.csv (column 'domein' or 'domain'). A missing file means an empty set."""
    if not os.path.exists(path):
        logger.info("No suppression.csv found at %s - nothing will be excluded.", path)
        return set()

    domains: set[str] = set()
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw = (row.get("domein") or row.get("domain") or "").strip().lower()
            if raw:
                domains.add(raw.removeprefix("www."))
    return domains


def apply_suppression(leads: list[LeadState], suppression_domains: set[str]) -> list[LeadState]:
    """Mark every 'ok' lead on a suppressed domain as 'skipped'. Mutates in place."""
    if not suppression_domains:
        return leads
    for lead in leads:
        if lead["status"] != "ok":
            continue
        domain = _domain(lead["website"])
        if domain and domain in suppression_domains:
            lead["status"] = "skipped"
            lead["error_reason"] = "Domain is on the suppression list"
            logger.info("Lead suppressed (suppression list): %s (%s)", lead["company_name"], domain)
    return leads
