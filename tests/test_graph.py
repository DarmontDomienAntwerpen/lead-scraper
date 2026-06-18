from src.graph import _has_active_leads, build_graph
from src.state import new_lead


def _lead_with_status(status: str) -> dict:
    lead = new_lead(
        zoekterm="x",
        place_id="p1",
        bedrijfsnaam="X",
        website="https://x.be",
        telefoon=None,
        bron="Google Places",
        opgehaald_op="2026-06-18T10:00:00",
    )
    lead["status"] = status
    return lead


def test_has_active_leads_continues_when_pending_present():
    state = {"leads": [_lead_with_status("pending")]}
    assert _has_active_leads(state) == "continue"


def test_has_active_leads_continues_when_ok_present():
    state = {"leads": [_lead_with_status("error"), _lead_with_status("ok")]}
    assert _has_active_leads(state) == "continue"


def test_has_active_leads_skips_when_all_terminal():
    state = {"leads": [_lead_with_status("error"), _lead_with_status("skipped")]}
    assert _has_active_leads(state) == "skip"


def test_has_active_leads_skips_on_empty_batch():
    assert _has_active_leads({"leads": []}) == "skip"


def test_build_graph_compiles_without_error():
    graph = build_graph()
    assert graph is not None
