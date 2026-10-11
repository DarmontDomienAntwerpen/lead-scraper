"""CLI entry point: builds the graph, runs one pipeline pass and reports the result.

Nothing is ever sent automatically: this script only produces an Excel file that
must be reviewed by a human before any email goes out.
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from src.config import DEFAULT_MAX_LEADS, DEFAULT_REGION, MIN_NEED_SCORE, require_api_keys
from src.graph import build_graph
from src.state import PipelineState

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Local lead-generation pipeline for B2B prospecting."
    )
    parser.add_argument(
        "--query", "--zoekterm", dest="search_term", required=True,
        help="Type of business to search for, e.g. 'loodgieter' (plumber) or 'fotograaf'",
    )
    parser.add_argument(
        "--max-leads", type=int, default=DEFAULT_MAX_LEADS, help=f"Default {DEFAULT_MAX_LEADS}"
    )
    parser.add_argument(
        "--region", "--regio", dest="region", default=DEFAULT_REGION, help=f"Default '{DEFAULT_REGION}'"
    )
    parser.add_argument(
        "--min-score",
        type=int,
        default=MIN_NEED_SCORE,
        help=(
            f"Minimum need score; leads below it skip the analyze/write steps "
            f"(saves API costs). Default {MIN_NEED_SCORE} (0 = keep everyone)."
        ),
    )
    return parser.parse_args()


async def run(search_term: str, region: str, max_leads: int, min_score: int = MIN_NEED_SCORE) -> str:
    require_api_keys()
    graph = build_graph()
    initial_state: PipelineState = {
        "search_term": search_term,
        "region": region,
        "max_leads": max_leads,
        "leads": [],
        "output_path": None,
        "min_need_score": min_score,
    }
    final_state = await graph.ainvoke(initial_state)
    return final_state["output_path"]


def main() -> None:
    args = parse_args()
    path = asyncio.run(run(args.search_term, args.region, args.max_leads, args.min_score))
    logger.info("Pipeline finished. Excel file: %s", path)
    print(f"\nExcel file written: {path}")
    print("IMPORTANT: review every lead manually before sending. Nothing is sent automatically.\n")


if __name__ == "__main__":
    main()
