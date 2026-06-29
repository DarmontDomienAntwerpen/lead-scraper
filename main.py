"""CLI entrypoint: bouwt de graph, draait één pipeline-run, en rapporteert het resultaat.

Niets wordt automatisch verstuurd — dit script produceert enkel een Excel-bestand
dat de gebruiker manueel moet naleren vóór er ooit gemaild wordt.
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from src.config import DEFAULT_MAX_LEADS, DEFAULT_REGIO, MIN_BEHOEFTE_SCORE, require_api_keys
from src.graph import build_graph
from src.state import PipelineState

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Lokale lead-generatie pipeline voor B2B-prospectie (Antwerpen)."
    )
    parser.add_argument(
        "--zoekterm", required=True, help="Bv. 'loodgieter', 'fotograaf', 'boekhouder'"
    )
    parser.add_argument(
        "--max-leads", type=int, default=DEFAULT_MAX_LEADS, help=f"Standaard {DEFAULT_MAX_LEADS}"
    )
    parser.add_argument("--regio", default=DEFAULT_REGIO, help=f"Standaard '{DEFAULT_REGIO}'")
    parser.add_argument(
        "--min-score",
        type=int,
        default=MIN_BEHOEFTE_SCORE,
        help=(
            f"Drempel voor de behoefte-score; leads eronder slaan analyze/write over "
            f"(scheelt API-kosten). Standaard {MIN_BEHOEFTE_SCORE} (0 = niemand uitgefilterd)."
        ),
    )
    return parser.parse_args()


async def run(zoekterm: str, regio: str, max_leads: int, min_score: int = MIN_BEHOEFTE_SCORE) -> str:
    require_api_keys()
    graph = build_graph()
    initial_state: PipelineState = {
        "zoekterm": zoekterm,
        "regio": regio,
        "max_leads": max_leads,
        "leads": [],
        "output_path": None,
        "min_behoefte_score": min_score,
    }
    final_state = await graph.ainvoke(initial_state)
    return final_state["output_path"]


def main() -> None:
    args = parse_args()
    path = asyncio.run(run(args.zoekterm, args.regio, args.max_leads, args.min_score))
    logger.info("Pipeline klaar. Excel-bestand: %s", path)
    print(f"\nExcel-bestand gegenereerd: {path}")
    print("BELANGRIJK: lees elke lead manueel na vóór verzending. Niets wordt automatisch verstuurd.\n")


if __name__ == "__main__":
    main()
