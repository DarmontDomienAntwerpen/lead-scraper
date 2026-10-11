"""Central configuration: loads .env and holds the constants shared by all nodes."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY: str | None = os.getenv("ANTHROPIC_API_KEY")
GOOGLE_PLACES_API_KEY: str | None = os.getenv("GOOGLE_PLACES_API_KEY")
# Optional: without a key, email verification is skipped (found addresses stay
# unverified in the export; nothing crashes).
NEVERBOUNCE_API_KEY: str | None = os.getenv("NEVERBOUNCE_API_KEY")

ANTHROPIC_MODEL = "claude-sonnet-4-6"

DEFAULT_REGION = "Antwerpen"
DEFAULT_MAX_LEADS = 20

# Polite scraping: minimum delay between consecutive scrape requests (seconds).
SCRAPE_DELAY_SECONDS = 2.0
SCRAPE_TIMEOUT_MS = 15_000
SCRAPE_MAX_CHARS = 6000
# Unstripped HTML for signal detection (forms/mailto/widget scripts) is stored separately
# from raw_text, and also capped so an unusually large page doesn't waste memory.
SIGNAL_HTML_MAX_CHARS = 50_000

# Threshold for the need score (src/nodes/signals.py): leads below it are marked "skipped"
# before analyze/write (the expensive LLM steps). Scoring: strong manual signals
# (quote_via_mail/phone_only_cta) add 2 points each, mailto_contact 1 point; every tool
# found (form/booking/webshop/livechat/email marketing) subtracts 1-2 points.
# Configurable via --min-score or the MIN_NEED_SCORE env var (0 = keep everyone).
# MIN_BEHOEFTE_SCORE is still accepted as the legacy name.
MIN_NEED_SCORE = int(os.getenv("MIN_NEED_SCORE", os.getenv("MIN_BEHOEFTE_SCORE", "1")))

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
VIEWPORT = {"width": 1366, "height": 768}

SUPPRESSION_CSV = "data/suppression.csv"
OUTPUT_DIR = "data/output"


@dataclass(frozen=True)
class RunConfig:
    """Parameters for one pipeline run, coming from the CLI."""

    search_term: str
    region: str = DEFAULT_REGION
    max_leads: int = DEFAULT_MAX_LEADS


def require_api_keys() -> None:
    """Fail early and clearly when required API keys are missing."""
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
            f"Missing environment variables: {', '.join(missing)}. "
            "Copy .env.example to .env and fill in the keys."
        )
