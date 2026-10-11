"""Wires the StateGraph: search -> scrape -> signals -> analyze -> write -> export.

Conditional edges route at batch level: as soon as NO lead is "active" any more
(status pending or ok, i.e. not yet final error/skipped), the next node has
nothing to do and the graph jumps straight to export. This avoids pointless
Playwright/LLM calls on an empty or fully rejected batch, without crashing the run.
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from src.config import MIN_NEED_SCORE
from src.nodes.analyze import analyze_leads
from src.nodes.export import export_leads
from src.nodes.scrape import scrape_leads
from src.nodes.search import search_leads
from src.nodes.signals import apply_signals
from src.nodes.write import write_mails
from src.state import PipelineState

_ACTIVE_STATUSES = ("pending", "ok")


async def _search_node(state: PipelineState) -> dict:
    leads = await search_leads(state["search_term"], state["region"], state["max_leads"])
    return {"leads": leads}


async def _scrape_node(state: PipelineState) -> dict:
    leads = await scrape_leads(state["leads"])
    return {"leads": leads}


def _signals_node(state: PipelineState) -> dict:
    min_score = state.get("min_need_score", MIN_NEED_SCORE)
    leads = apply_signals(state["leads"], min_score=min_score)
    return {"leads": leads}


async def _analyze_node(state: PipelineState) -> dict:
    leads = await analyze_leads(state["leads"])
    return {"leads": leads}


async def _write_node(state: PipelineState) -> dict:
    leads = await write_mails(state["leads"])
    return {"leads": leads}


def _export_node(state: PipelineState) -> dict:
    path = export_leads(state["leads"], state["region"])
    return {"output_path": path}


def _has_active_leads(state: PipelineState) -> str:
    if any(lead["status"] in _ACTIVE_STATUSES for lead in state["leads"]):
        return "continue"
    return "skip"


def build_graph():
    """Compile and return the executable StateGraph."""
    graph = StateGraph(PipelineState)
    graph.add_node("search", _search_node)
    graph.add_node("scrape", _scrape_node)
    graph.add_node("signals", _signals_node)
    graph.add_node("analyze", _analyze_node)
    graph.add_node("write", _write_node)
    graph.add_node("export", _export_node)

    graph.set_entry_point("search")
    graph.add_conditional_edges("search", _has_active_leads, {"continue": "scrape", "skip": "export"})
    graph.add_conditional_edges("scrape", _has_active_leads, {"continue": "signals", "skip": "export"})
    graph.add_conditional_edges("signals", _has_active_leads, {"continue": "analyze", "skip": "export"})
    graph.add_conditional_edges("analyze", _has_active_leads, {"continue": "write", "skip": "export"})
    graph.add_edge("write", "export")
    graph.add_edge("export", END)

    return graph.compile()
