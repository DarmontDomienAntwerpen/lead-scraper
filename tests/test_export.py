from openpyxl import load_workbook

from src.nodes.export import COLUMN_HEADERS, export_leads
from src.state import new_lead


def _make_lead(status: str = "ok", website: str = "https://peeters-fietsen.be") -> dict:
    lead = new_lead(
        zoekterm="fietsenmaker",
        place_id="p1",
        bedrijfsnaam="Fietsen Peeters",
        website=website,
        telefoon="03 111 22 33",
        bron="Google Places",
        opgehaald_op="2026-06-18T10:00:00",
    )
    lead["niche"] = "fietsenmaker gespecialiseerd in elektrische fietsen"
    lead["ijsbreker"] = "Ik zag dat jullie e-bikes herstellen."
    lead["mail"] = "Korte mail tekst."
    lead["status"] = status
    return lead


def test_export_leads_writes_expected_columns_and_filters_status(tmp_path):
    ok_lead = _make_lead(status="ok")
    skipped_lead = _make_lead(status="skipped", website="https://andere.be")
    skipped_lead["place_id"] = "p2"

    path = export_leads([ok_lead, skipped_lead], regio="Antwerpen", output_dir=str(tmp_path))

    wb = load_workbook(path)
    assert wb.sheetnames == ["Leads", "bron_log"]

    leads_sheet = wb["Leads"]
    header = [cell.value for cell in leads_sheet[1]]
    assert header == COLUMN_HEADERS == [
        "Bedrijfsnaam",
        "Website",
        "Telefoon",
        "Email",
        "Niche",
        "Openingszin",
        "Gepersonaliseerde Mail",
    ]
    assert leads_sheet.max_row == 2  # header + 1 ok-lead
    assert leads_sheet.cell(row=2, column=1).value == "Fietsen Peeters"
    assert leads_sheet.freeze_panes == "A2"

    log_sheet = wb["bron_log"]
    assert log_sheet.max_row == 3  # header + 2 leads (ok + skipped)


def test_export_leads_applies_suppression(tmp_path):
    suppression_path = tmp_path / "suppression.csv"
    suppression_path.write_text("domein\npeeters-fietsen.be\n")

    import src.nodes.export as export_module

    original = export_module.load_suppression_domains
    export_module.load_suppression_domains = lambda path=None: original(str(suppression_path))
    try:
        lead = _make_lead(status="ok")
        path = export_leads([lead], regio="Antwerpen", output_dir=str(tmp_path))
    finally:
        export_module.load_suppression_domains = original

    wb = load_workbook(path)
    assert wb["Leads"].max_row == 1  # enkel header, lead werd onderdrukt
