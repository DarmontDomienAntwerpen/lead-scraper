from types import SimpleNamespace

import pytest

from src.nodes.write import _validate_mail, _validate_onderwerpregel, write_mails
from src.state import new_lead


def _make_ok_lead() -> dict:
    lead = new_lead(
        zoekterm="fietsenmaker",
        place_id="p1",
        bedrijfsnaam="Fietsen Peeters",
        website="https://peeters-fietsen.be",
        telefoon="03 111 22 33",
        bron="Google Places",
        opgehaald_op="2026-06-18T10:00:00",
    )
    lead["niche"] = "fietsenmaker gespecialiseerd in elektrische fietsen"
    lead["ijsbreker"] = "Ik zag dat jullie gespecialiseerd zijn in e-bike-herstellingen."
    lead["haakje"] = "Intake loopt via e-mail in plaats van een gekoppeld formulier."
    lead["status"] = "ok"
    return lead


def test_validate_mail_flags_too_many_sentences():
    mail = "Een. Twee. Drie. Vier. Vijf."
    problemen = _validate_mail(mail)
    assert any("zinnen" in p for p in problemen)


def test_validate_mail_flags_formal_u():
    mail = "Ik zag dat u een fietsenwinkel heeft. Mag ik u een demo sturen? Laat het weten als je dit niet wil ontvangen."
    problemen = _validate_mail(mail)
    assert any("u" in p for p in problemen)


def test_validate_mail_requires_opt_out():
    mail = "Hoi! Mooie website. Mag ik een videodemo van 1 minuut sturen?"
    problemen = _validate_mail(mail)
    assert any("opt-out" in p for p in problemen)


def test_validate_mail_flags_spam_trigger_words():
    mail = (
        "Ik zag dat jullie gespecialiseerd zijn in e-bike-herstellingen. "
        "Profiteer nu van een gratis kennismaking met korting! "
        "Mag ik een kort gesprek inplannen? "
        "Laat het gerust weten als je dit niet wil ontvangen."
    )
    problemen = _validate_mail(mail)
    assert any("spam" in p for p in problemen)


def test_validate_mail_flags_excessive_exclamation_marks():
    mail = (
        "Wow, mooie website! Echt indrukwekkend! "
        "Mag ik een kort gesprek inplannen om te horen wat bij jullie veel tijd kost? "
        "Laat het gerust weten als je dit niet wil ontvangen."
    )
    problemen = _validate_mail(mail)
    assert any("uitroeptekens" in p for p in problemen)


def test_validate_mail_passes_compliant_mail():
    mail = (
        "Ik zag dat jullie gespecialiseerd zijn in e-bike-herstellingen. "
        "Met automatisering hou je veel tijd over op het opmaken van offertes en planning. "
        "Mag ik een kort gesprek inplannen om te horen wat bij jullie veel tijd kost? "
        "Laat het gerust weten als je dit niet wil ontvangen."
    )
    assert _validate_mail(mail) == []


def test_validate_mail_flags_mooie_website():
    mail = (
        "Ik zag jullie mooie website en de e-bike-herstellingen. "
        "Mag ik een kort gesprek inplannen om te horen wat bij jullie veel tijd kost? "
        "Laat het gerust weten als je dit niet wil ontvangen."
    )
    problemen = _validate_mail(mail)
    assert any("mooie website" in p for p in problemen)


def test_validate_mail_flags_repeated_automatisering():
    mail = (
        "Jullie offertes gaan via mail. Automatisering hiervan bespaart tijd, en automatisering "
        "van de planning ook. Mag ik een kort gesprek inplannen? "
        "Laat het gerust weten als je dit niet wil ontvangen."
    )
    problemen = _validate_mail(mail)
    assert any("automatisering" in p for p in problemen)


def test_validate_onderwerpregel_flags_empty():
    assert _validate_onderwerpregel("") != []


def test_validate_onderwerpregel_flags_too_many_words():
    problemen = _validate_onderwerpregel("Dit is een veel te lange onderwerpregel met te veel woorden")
    assert any("woorden" in p for p in problemen)


def test_validate_onderwerpregel_passes_short_subject():
    assert _validate_onderwerpregel("Offertes overtypen bij Peeters?") == []


class FakeLLM:
    def __init__(self, responses: list[str]):
        self.responses = responses
        self.calls = 0

    async def ainvoke(self, messages):
        content = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return SimpleNamespace(content=content)


@pytest.mark.asyncio
async def test_write_mails_accepts_compliant_first_try():
    lead = _make_ok_lead()
    compliant_mail = (
        "Jullie intake loopt via e-mail in plaats van een gekoppeld formulier. "
        "Met automatisering hou je veel tijd over op het opmaken van offertes. "
        "Herkenbaar bij jullie? "
        "Laat het gerust weten als je dit niet wil ontvangen."
    )
    fake = FakeLLM([f"ONDERWERP: Offertes overtypen?\nMAIL: {compliant_mail}"])
    result = await write_mails([lead], llm=fake)
    assert result[0]["mail"] == compliant_mail
    assert result[0]["onderwerpregel"] == "Offertes overtypen?"
    assert fake.calls == 1


@pytest.mark.asyncio
async def test_write_mails_regenerates_once_on_violation():
    lead = _make_ok_lead()
    bad = "ONDERWERP: Hoi\nMAIL: Ik zag dat u een mooie website heeft."
    good_mail = (
        "Jullie intake loopt via e-mail in plaats van een gekoppeld formulier. "
        "Met automatisering hou je veel tijd over op het opmaken van offertes. "
        "Herkenbaar bij jullie? "
        "Laat het gerust weten als je dit niet wil ontvangen."
    )
    good = f"ONDERWERP: Offertes overtypen?\nMAIL: {good_mail}"
    fake = FakeLLM([bad, good])
    result = await write_mails([lead], llm=fake)
    assert fake.calls == 2
    assert result[0]["mail"] == good_mail
    assert result[0]["status"] == "ok"


@pytest.mark.asyncio
async def test_write_mails_keeps_lead_after_failed_regeneration_but_flags_it():
    lead = _make_ok_lead()
    bad = "ONDERWERP: Hoi\nMAIL: Ik zag dat u een mooie website heeft."
    fake = FakeLLM([bad, bad])
    result = await write_mails([lead], llm=fake)
    assert result[0]["mail"] == "Ik zag dat u een mooie website heeft."
    assert result[0]["status"] == "ok"
    assert "Mail-validatie" in result[0]["error_reden"]


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
    assert "rate limited" in result[0]["error_reden"]
