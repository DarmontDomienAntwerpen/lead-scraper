from src.nodes.signals import apply_signals, compute_score, detect_signals, select_haakje
from src.state import new_lead


def _make_lead(ruwe_html: str) -> dict:
    lead = new_lead(
        zoekterm="loodgieter",
        place_id="p1",
        bedrijfsnaam="Loodgieter Janssens",
        website="https://janssens.be",
        telefoon=None,
        bron="Google Places",
        opgehaald_op="2026-06-18T10:00:00",
    )
    lead["ruwe_html"] = ruwe_html
    lead["ruwe_tekst"] = "fallback tekst"
    return lead


def test_detect_signals_finds_mailto_and_form():
    html = '<a href="mailto:info@janssens.be">Mail</a><form><input></form>'
    signalen = detect_signals(html)
    assert signalen["mailto_contact"] is True
    assert signalen["has_form"] is True


def test_detect_signals_finds_booking_tool():
    html = '<script src="https://calendly.com/widget.js"></script>'
    signalen = detect_signals(html)
    assert signalen["has_booking_tool"] is True


def test_detect_signals_phone_only_cta_requires_no_form_or_booking():
    html = "<p>Bel ons op 03 222 33 44 voor een afspraak.</p>"
    signalen = detect_signals(html)
    assert signalen["phone_only_cta"] is True

    html_with_form = html + "<form></form>"
    signalen_with_form = detect_signals(html_with_form)
    assert signalen_with_form["phone_only_cta"] is False


def test_detect_signals_offerte_via_mail_requires_no_form():
    html = "<p>Offerte aanvragen kan via een mailtje.</p>"
    assert detect_signals(html)["offerte_via_mail"] is True
    assert detect_signals(html + "<form></form>")["offerte_via_mail"] is False


def test_compute_score_rewards_manual_penalizes_tools():
    manual_only = {"offerte_via_mail": True, "phone_only_cta": False, "mailto_contact": False}
    assert compute_score(manual_only) == 2

    manual_with_tool = dict(manual_only, has_booking_tool=True)
    assert compute_score(manual_with_tool) == 0  # geklemd op 0, nooit negatief


def test_select_haakje_prioritizes_offerte_via_mail():
    signalen = {"offerte_via_mail": True, "phone_only_cta": True, "mailto_contact": True}
    assert "Offertes" in select_haakje(signalen)


def test_select_haakje_empty_when_no_manual_signal():
    assert select_haakje({"has_form": True}) == ""


def test_apply_signals_skips_leads_below_threshold():
    lead = _make_lead("<p>Modern bedrijf met formulier.</p><form></form>")
    apply_signals([lead], min_score=1)
    assert lead["status"] == "skipped"
    assert "Behoefte-score" in lead["error_reden"]


def test_apply_signals_keeps_leads_above_threshold():
    lead = _make_lead("<p>Offerte aanvragen via mail, bel ons op 03 222 33 44.</p>")
    apply_signals([lead], min_score=1)
    assert lead["status"] == "pending"
    assert lead["behoefte_score"] >= 1
    assert lead["haakje"]


def test_apply_signals_threshold_zero_lets_everyone_through():
    lead = _make_lead("<p>Niets bijzonders hier.</p>")
    apply_signals([lead], min_score=0)
    assert lead["status"] == "pending"


def test_apply_signals_default_threshold_filters_weak_leads():
    lead = _make_lead("<p>Niets bijzonders hier.</p>")
    apply_signals([lead])  # gebruikt de standaard MIN_BEHOEFTE_SCORE (2)
    assert lead["status"] == "skipped"


def test_apply_signals_ignores_non_pending_leads():
    lead = _make_lead("<p>Offerte aanvragen via mail.</p>")
    lead["status"] = "error"
    apply_signals([lead], min_score=5)
    assert lead["behoefte_score"] == 0
    assert lead["status"] == "error"
