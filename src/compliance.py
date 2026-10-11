"""ARCHITECT/OPTIMIZER: compliance — suppression-list en bron-traceerbaarheid.

Twee verantwoordelijkheden:
1. `load_suppression_domains` / `apply_suppression`: domeinen op de
   suppression-list worden nooit meegenomen in de export, ongeacht hoe goed
   de rest van de pipeline ze beoordeelde.
2. Elke `LeadState` draagt al `bron`, `opgehaald_op` en `zoekterm` (zie
   state.py) zodat export.py dit kan hergebruiken voor het "bron_log"-tabblad
   — geen aparte logstructuur nodig.
"""

from __future__ import annotations

import csv
import logging
import os
from urllib.parse import urlparse

from src.config import SUPPRESSION_CSV
from src.state import LeadState

logger = logging.getLogger(__name__)

# Publieke e-maildiensten mogen nooit als bedrijfsdomein op de suppression-list komen:
# één zelfstandige met een gmail-adres zou anders alle toekomstige gmail-leads blokkeren.
FREEMAIL_DOMAINS = {
    "gmail.com", "hotmail.com", "outlook.com", "live.com", "yahoo.com",
    "icloud.com", "telenet.be", "skynet.be", "proximus.be",
}


def is_suppressible_domain(domain: str) -> bool:
    """False voor publieke maildiensten - die horen nooit op de suppression-list."""
    return domain.lower() not in FREEMAIL_DOMAINS


def _domain(url: str | None) -> str | None:
    if not url:
        return None
    netloc = urlparse(url if "://" in url else f"https://{url}").netloc.lower()
    return netloc.removeprefix("www.") or None


def load_suppression_domains(path: str = SUPPRESSION_CSV) -> set[str]:
    """Leest suppression.csv (kolom 'domein' of 'domain'). Ontbrekend bestand = lege set."""
    if not os.path.exists(path):
        logger.info("Geen suppression.csv gevonden op %s - er wordt niets uitgesloten.", path)
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
    """Zet status van elke 'ok'-lead op een suppressed domein naar 'skipped'. Muteert in-place."""
    if not suppression_domains:
        return leads
    for lead in leads:
        if lead["status"] != "ok":
            continue
        domain = _domain(lead["website"])
        if domain and domain in suppression_domains:
            lead["status"] = "skipped"
            lead["error_reden"] = "Domein staat op de suppression-list"
            logger.info("Lead onderdrukt (suppression-list): %s (%s)", lead["bedrijfsnaam"], domain)
    return leads
