"""ARCHITECT: centrale configuratie — laadt .env en bundelt constanten die door alle nodes gebruikt worden."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY: str | None = os.getenv("ANTHROPIC_API_KEY")
GOOGLE_PLACES_API_KEY: str | None = os.getenv("GOOGLE_PLACES_API_KEY")
# Optioneel: zonder key wordt e-mailverificatie gewoon overgeslagen (gevonden e-mails
# blijven dan ongeverifieerd in de export staan, niets crasht).
NEVERBOUNCE_API_KEY: str | None = os.getenv("NEVERBOUNCE_API_KEY")

ANTHROPIC_MODEL = "claude-sonnet-4-6"

DEFAULT_REGIO = "Antwerpen"
DEFAULT_MAX_LEADS = 20

# Beleefd scrapen: minimale wachttijd tussen opeenvolgende scrape-requests (seconden).
SCRAPE_DELAY_SECONDS = 2.0
SCRAPE_TIMEOUT_MS = 15_000
SCRAPE_MAX_CHARS = 6000
# Ongestripte HTML voor signaaldetectie (forms/mailto/widget-scripts) wordt los van
# ruwe_tekst bewaard, maar ook hier afgekapt zodat een uitzonderlijk grote pagina het
# geheugen niet onnodig belast.
SIGNAL_HTML_MAX_CHARS = 50_000

# Drempel voor de behoefte-score (src/nodes/signals.py): leads die hieronder vallen worden
# vóór analyze/write (de dure LLM-stappen) als "skipped" gemarkeerd. Score-opbouw: sterke
# handmatige signalen (offerte_via_mail/phone_only_cta) wegen elk 2 punten, mailto_contact
# 1 punt; elke aanwezige tool (form/booking/webshop/livechat/email-marketing) trekt 1-2 punten
# af. Standaard 2 = minstens één écht sterk handmatig signaal vereist, niet enkel een losse
# mailto-link. Instelbaar via --min-score of de MIN_BEHOEFTE_SCORE env-var (0 = niemand
# uitgefilterd).
MIN_BEHOEFTE_SCORE = int(os.getenv("MIN_BEHOEFTE_SCORE", "1"))

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
VIEWPORT = {"width": 1366, "height": 768}

SUPPRESSION_CSV = "data/suppression.csv"
OUTPUT_DIR = "data/output"


@dataclass(frozen=True)
class RunConfig:
    """Parameters voor één pipeline-run, afkomstig van de CLI."""

    zoekterm: str
    regio: str = DEFAULT_REGIO
    max_leads: int = DEFAULT_MAX_LEADS


def require_api_keys() -> None:
    """Faalt vroeg en duidelijk als verplichte API-keys ontbreken."""
    missing = [
        name
        for name, value in (
            ("ANTHROPIC_API_KEY", ANTHROPIC_API_KEY),
            ("GOOGLE_PLACES_API_KEY", GOOGLE_PLACES_API_KEY),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            f"Ontbrekende environment variabelen: {', '.join(missing)}. "
            "Kopieer .env.example naar .env en vul de keys in."
        )
