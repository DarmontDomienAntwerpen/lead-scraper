"""End-to-end test van de volledige StateGraph met alle externe calls gemockt."""

from unittest.mock import AsyncMock, patch

import pytest
from openpyxl import load_workbook

import src.graph as graph_module
from src.nodes.export import export_leads as real_export_leads
from src.state import new_lead


def _export_to_tmp(tmp_path):
    def _export(leads, regio):
        return real_export_leads(leads, regio, output_dir=str(tmp_path))

    return _export


def _stub_lead(place_id: str, with_website: bool = True) -> dict:
    return new_lead(
        zoekterm="loodgieter",
        place_id=place_id,
        bedrijfsnaam=f"Bedrijf {place_id}",
        website="https://example.be" if with_website else None,
        telefoon="03 000 00 00",
        bron="Google Places",
        opgehaald_op="2026-06-18T10:00:00",
    )


@pytest.mark.asyncio
async def test_full_graph_runs_search_to_export_with_mixed_outcomes(tmp_path):
    leads = [_stub_lead("p1"), _stub_lead("p2"), _stub_lead("p3")]

    async def fake_search(zoekterm, regio, max_leads):
        return leads

    async def fake_scrape(batch):
        batch[0]["ruwe_tekst"] = "Echte inhoud over loodgieterswerk."
        batch[1]["status"] = "error"
        batch[1]["error_reden"] = "Timeout"
        batch[2]["ruwe_tekst"] = "Cookie-muur pagina."
        return batch

    async def fake_analyze(batch):
        for lead in batch:
            if lead["status"] != "pending":
                continue
            if "Cookie" in lead["ruwe_tekst"]:
                lead["status"] = "skipped"
                lead["error_reden"] = "Bad data"
            else:
                lead["niche"] = "loodgieter gespecialiseerd in lekdetectie"
                lead["ijsbreker"] = "Ik zag dat jullie lekdetectie aanbieden."
                lead["status"] = "ok"
        return batch

    async def fake_write(batch):
        for lead in batch:
            if lead["status"] == "ok":
                lead["mail"] = (
                    "Ik zag dat jullie lekdetectie aanbieden. "
                    "Met AI-automatisering bespaar je tot 10 uur per week op planning. "
                    "Mag ik een kort gesprek inplannen om te horen wat bij jullie veel tijd kost? "
                    "Laat het gerust weten als je dit niet wil ontvangen."
                )
        return batch

    with patch.object(graph_module, "search_leads", AsyncMock(side_effect=fake_search)), \
         patch.object(graph_module, "scrape_leads", AsyncMock(side_effect=fake_scrape)), \
         patch.object(graph_module, "analyze_leads", AsyncMock(side_effect=fake_analyze)), \
         patch.object(graph_module, "write_mails", AsyncMock(side_effect=fake_write)), \
         patch.object(graph_module, "export_leads", side_effect=_export_to_tmp(tmp_path)):
        compiled = graph_module.build_graph()
        final_state = await compiled.ainvoke(
            {"zoekterm": "loodgieter", "regio": "Antwerpen", "max_leads": 20, "leads": [], "output_path": None}
        )

    assert final_state["output_path"]
    wb = load_workbook(final_state["output_path"])
    assert wb["Leads"].max_row == 2  # header + de ene geslaagde lead (p1)
    assert wb["Leads"].cell(row=2, column=1).value == "Bedrijf p1"
    assert wb["bron_log"].max_row == 4  # header + alle 3 leads


@pytest.mark.asyncio
async def test_full_graph_skips_to_export_when_search_finds_nothing(tmp_path):
    async def fake_search(zoekterm, regio, max_leads):
        return []

    with patch.object(graph_module, "search_leads", AsyncMock(side_effect=fake_search)), \
         patch.object(graph_module, "export_leads", side_effect=_export_to_tmp(tmp_path)):
        compiled = graph_module.build_graph()
        final_state = await compiled.ainvoke(
            {"zoekterm": "x", "regio": "Antwerpen", "max_leads": 20, "leads": [], "output_path": None}
        )

    wb = load_workbook(final_state["output_path"])
    assert wb["Leads"].max_row == 1  # enkel header
