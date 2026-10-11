from types import SimpleNamespace

import pytest

from src.nodes.write import _validate_mail, _validate_subject_line, write_mails, SIGNATURE, _EXTRA_LINE, strip_body
from src.state import new_lead


def _make_ok_lead() -> dict:
    lead = new_lead(
        search_term="fietsenmaker",
        place_id="p1",
        company_name="Fietsen Peeters",
        website="https://peeters-fietsen.be",
        phone="03 111 22 33",
        source="Google Places",
        fetched_at="2026-06-18T10:00:00",
    )
    lead["niche"] = "fietsenmaker gespecialiseerd in elektrische fietsen"
    lead["icebreaker"] = "Ik zag dat jullie gespecialiseerd zijn in e-bike-herstellingen."
    lead["hook"] = "Intake loopt via e-mail in plaats van een gekoppeld formulier."
    lead["status"] = "ok"
    return lead


def test_validate_mail_flags_too_many_sentences():
    mail = "Een. Twee. Drie. Vier. Vijf."
    problems = _validate_mail(mail)
    assert any("zinnen" in p for p in problems)


def test_validate_mail_flags_formal_u():
    mail = "Ik zag dat u een fietsenwinkel heeft. Mag ik u een demo sturen? Laat het weten als je dit niet wil ontvangen."
    problems = _validate_mail(mail)
    assert any("u" in p for p in problems)


def test_validate_mail_flags_too_short_mail():
    mail = "Hoi!"
    problems = _validate_mail(mail)
    assert problems == []  # korte mail is geen validatiefout (enkel kwaliteitsissue via LLM-rechter)


def test_validate_mail_flags_spam_trigger_words():
    mail = (
        "Ik zag dat jullie gespecialiseerd zijn in e-bike-herstellingen. "
        "Profiteer nu van een gratis kennismaking met korting! "
        "Mag ik een kort gesprek inplannen? "
        "Laat het gerust weten als je dit niet wil ontvangen."
    )
    problems = _validate_mail(mail)
    assert any("spam" in p for p in problems)


def test_validate_mail_flags_excessive_exclamation_marks():
    mail = (
        "Wow, mooie website! Echt indrukwekkend! "
        "Mag ik een kort gesprek inplannen om te horen wat bij jullie veel tijd kost? "
        "Laat het gerust weten als je dit niet wil ontvangen."
    )
    problems = _validate_mail(mail)
    assert any("uitroeptekens" in p for p in problems)


def test_validate_mail_passes_compliant_mail():
    mail = (
        "Ik zag dat jullie gespecialiseerd zijn in e-bike-herstellingen. "
        "Met automatisering hou je veel tijd over op het opmaken van offertes en planning. "
        "Ik bekijk graag in 15 minuten wat voor jullie het meeste oplevert."
    )
    assert _validate_mail(mail) == []


def test_validate_mail_flags_generic_compliment():
    mail = (
        "Ik zag jullie mooie website en de e-bike-herstellingen. "
        "Mag ik een kort gesprek inplannen om te horen wat bij jullie veel tijd kost? "
        "Laat het gerust weten als je dit niet wil ontvangen."
    )
    problems = _validate_mail(mail)
    assert any("mooie website" in p for p in problems)


def test_validate_mail_flags_repeated_automation():
    mail = (
        "Jullie offertes gaan via mail. Automatisering hiervan bespaart tijd, en automatisering "
        "van de planning ook. Mag ik een kort gesprek inplannen? "
        "Laat het gerust weten als je dit niet wil ontvangen."
    )
    problems = _validate_mail(mail)
    assert any("automatisering" in p for p in problems)


def test_validate_subject_line_flags_empty():
    assert _validate_subject_line("") != []


def test_validate_subject_line_flags_too_many_words():
    problems = _validate_subject_line("Dit is een veel te lange onderwerpregel met te veel woorden")
    assert any("woorden" in p for p in problems)


def test_validate_subject_line_passes_short_subject():
    assert _validate_subject_line("Offertes overtypen bij Peeters?") == []


def test_strip_body_removes_greeting_and_signature():
    body = "Eerste zin.\n\nTweede zin.\n\nDerde zin."
    full = f"Beste Loodgieter BV,\n\n{body}\n\n{_EXTRA_LINE}\n\n{SIGNATURE}"
    assert strip_body(full) == body


def test_strip_body_removes_double_extra_line():
    body = "Eerste zin.\n\nTweede zin.\n\nDerde zin."
    broken = f"Beste BV,\n\n{body}\n\n{_EXTRA_LINE}\n\n{_EXTRA_LINE}\n\n{SIGNATURE}"
    assert strip_body(broken) == body


def test_strip_body_handles_no_greeting():
    body = "Eerste zin.\n\nTweede zin."
    assert strip_body(body) == body


def test_validate_mail_flags_literal_duplicate_sentence():
    mail = "Zin A is uniek.\n\nZin A is uniek.\n\nZin C is anders."
    problems = _validate_mail(mail)
    assert any("identieke" in p for p in problems)


def test_validate_mail_strips_extra_line_before_counting():
    body = "Zin 1.\n\nZin 2.\n\nZin 3."
    mail_with_extra = f"{body}\n\n{_EXTRA_LINE}"
    assert _validate_mail(mail_with_extra) == []


class FakeLLM:
    """Fake LLM: returns responses in order and repeats the last one when it runs out."""
    def __init__(self, responses: list[str]):
        self.responses = responses
        self.calls = 0

    async def ainvoke(self, messages):
        content = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return SimpleNamespace(content=content)


def _assembled(contact_name: str, body: str) -> str:
    """Expected output of _assemble_mail, for use in assertions."""
    return f"Beste {contact_name},\n\n{body}\n\n{_EXTRA_LINE}\n\n{SIGNATURE}"


@pytest.mark.asyncio
async def test_write_mails_accepts_compliant_first_try():
    lead = _make_ok_lead()
    body = (
        "Jullie intake loopt via e-mail in plaats van een gekoppeld formulier. "
        "Met automatisering hou je veel tijd over op het opmaken van offertes. "
        "Ik bekijk graag in 15 minuten wat voor jullie het meeste oplevert."
    )
    # FakeLLM: [1] email generation, [2] _judge_body answer -> "OK"
    fake = FakeLLM([f"ONDERWERP: Offertes overtypen?\nMAIL: {body}", "OK"])
    result = await write_mails([lead], llm=fake)
    assert result[0]["mail"] == _assembled("Fietsen Peeters", body)
    assert result[0]["subject_line"] == "Offertes overtypen?"
    assert fake.calls == 2  # 1 generate + 1 judge


@pytest.mark.asyncio
async def test_write_mails_regenerates_once_on_violation():
    lead = _make_ok_lead()
    bad = "ONDERWERP: Hoi\nMAIL: Ik zag dat u een mooie website heeft."
    good_body = (
        "Jullie intake loopt via e-mail in plaats van een gekoppeld formulier. "
        "Met automatisering hou je veel tijd over op het opmaken van offertes. "
        "Ik bekijk graag in 15 minuten wat voor jullie het meeste oplevert."
    )
    good = f"ONDERWERP: Offertes overtypen?\nMAIL: {good_body}"
    # [1] bad generate, [2] good regenerate, [3] judge → OK
    fake = FakeLLM([bad, good, "OK"])
    result = await write_mails([lead], llm=fake)
    assert fake.calls == 3
    assert result[0]["mail"] == _assembled("Fietsen Peeters", good_body)
    assert result[0]["status"] == "ok"


@pytest.mark.asyncio
async def test_write_mails_keeps_lead_after_failed_regeneration_but_flags_it():
    lead = _make_ok_lead()
    bad = "ONDERWERP: Hoi\nMAIL: Ik zag dat u een mooie website heeft."
    # [1] bad, [2] bad regenerate, [3] judge → OK (judge only runs on final mail)
    fake = FakeLLM([bad, bad, "OK"])
    result = await write_mails([lead], llm=fake)
    assert "Ik zag dat u een mooie website heeft." in result[0]["mail"]
    assert result[0]["status"] == "ok"
    assert "Email validation" in result[0]["error_reason"]


@pytest.mark.asyncio
async def test_write_mails_skips_non_ok_leads():
    lead = _make_ok_lead()
    lead["status"] = "skipped"
    fake = FakeLLM(["irrelevant"])
    result = await write_mails([lead], llm=fake)
    assert fake.calls == 0
    assert result[0]["mail"] == ""


@pytest.mark.asyncio
async def test_write_mails_handles_exception_gracefully():
    lead = _make_ok_lead()

    class ExplodingLLM:
        async def ainvoke(self, messages):
            raise RuntimeError("rate limited")

    result = await write_mails([lead], llm=ExplodingLLM())
    assert result[0]["status"] == "error"
    assert "rate limited" in result[0]["error_reason"]
