from src.compliance import apply_suppression, load_suppression_domains
from src.state import new_lead


def _make_lead(website: str, status: str = "ok") -> dict:
    lead = new_lead(
        zoekterm="test",
        place_id="p1",
        bedrijfsnaam="Testbedrijf",
        website=website,
        telefoon=None,
        bron="Google Places",
        opgehaald_op="2026-06-18T10:00:00",
    )
    lead["status"] = status
    return lead


def test_load_suppression_domains_missing_file_returns_empty_set(tmp_path):
    assert load_suppression_domains(str(tmp_path / "missing.csv")) == set()


def test_load_suppression_domains_strips_www(tmp_path):
    path = tmp_path / "suppression.csv"
    path.write_text("domein\nwww.geblokkeerd.be\nander-bedrijf.be\n")
    domains = load_suppression_domains(str(path))
    assert domains == {"geblokkeerd.be", "ander-bedrijf.be"}


def test_apply_suppression_marks_matching_domain_as_skipped():
    lead = _make_lead("https://www.geblokkeerd.be/contact")
    apply_suppression([lead], {"geblokkeerd.be"})
    assert lead["status"] == "skipped"
    assert "suppression" in lead["error_reden"].lower()


def test_apply_suppression_leaves_other_leads_untouched():
    lead = _make_lead("https://andere-zaak.be")
    apply_suppression([lead], {"geblokkeerd.be"})
    assert lead["status"] == "ok"


def test_apply_suppression_ignores_non_ok_leads():
    lead = _make_lead("https://www.geblokkeerd.be", status="error")
    apply_suppression([lead], {"geblokkeerd.be"})
    assert lead["status"] == "error"
