from src.nodes.signals import apply_signals, compute_score, detect_signals, select_hook
from src.state import new_lead


def _make_lead(raw_html: str) -> dict:
    lead = new_lead(
        search_term="loodgieter",
        place_id="p1",
        company_name="Loodgieter Janssens",
        website="https://janssens.be",
        phone=None,
        source="Google Places",
        fetched_at="2026-06-18T10:00:00",
    )
    lead["raw_html"] = raw_html
    lead["raw_text"] = "fallback tekst"
    return lead


def test_detect_signals_finds_mailto_and_form():
    html = '<a href="mailto:info@janssens.be">Mail</a><form><input></form>'
    signal_flags = detect_signals(html)
    assert signal_flags["mailto_contact"] is True
    assert signal_flags["has_form"] is True


def test_detect_signals_finds_booking_tool():
    html = '<script src="https://calendly.com/widget.js"></script>'
    signal_flags = detect_signals(html)
    assert signal_flags["has_booking_tool"] is True


def test_detect_signals_phone_only_cta_requires_no_booking_tool():
    html = "<p>Bel ons op 03 222 33 44 voor een afspraak.</p>"
    assert detect_signals(html)["phone_only_cta"] is True

    # A contact form does NOT block phone_only_cta (a form is not phone handling)
    html_with_form = html + "<form></form>"
    assert detect_signals(html_with_form)["phone_only_cta"] is True

    # but a real booking tool does
    html_with_booking = html + '<script src="fresha.com/widget"></script>'
    assert detect_signals(html_with_booking)["phone_only_cta"] is False


def test_detect_signals_quote_via_mail_requires_no_form():
    html = "<p>Offerte aanvragen kan via een mailtje.</p>"
    assert detect_signals(html)["quote_via_mail"] is True
    assert detect_signals(html + "<form></form>")["quote_via_mail"] is False


def test_compute_score_rewards_manual_penalizes_tools():
    manual_only = {"quote_via_mail": True, "phone_only_cta": False, "mailto_contact": False}
    assert compute_score(manual_only) == 2

    manual_with_tool = dict(manual_only, has_booking_tool=True)
    assert compute_score(manual_with_tool) == 1  # penalty (1) sluit signaal niet volledig uit

    manual_with_webshop = dict(manual_only, has_webshop=True)
    assert compute_score(manual_with_webshop) == 0  # zwaardere penalty (2), geklemd op 0


def test_select_hook_prioritizes_quote_via_mail():
    signal_flags = {"quote_via_mail": True, "phone_only_cta": True, "mailto_contact": True}
    assert "Offertes" in select_hook(signal_flags)


def test_select_hook_empty_when_no_manual_signal():
    assert select_hook({"has_form": True}) == ""


def test_apply_signals_skips_leads_below_threshold():
    lead = _make_lead("<p>Modern bedrijf met formulier.</p><form></form>")
    apply_signals([lead], min_score=1)
    assert lead["status"] == "skipped"
    assert "Need score" in lead["error_reason"]


def test_apply_signals_keeps_leads_above_threshold():
    lead = _make_lead("<p>Offerte aanvragen via mail, bel ons op 03 222 33 44.</p>")
    apply_signals([lead], min_score=1)
    assert lead["status"] == "pending"
    assert lead["need_score"] >= 1
    assert lead["hook"]


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
    assert lead["need_score"] == 0
    assert lead["status"] == "error"
