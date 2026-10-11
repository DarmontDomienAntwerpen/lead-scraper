import pytest

from src.nodes.analyze import AnalysisResult, analyze_leads
from src.state import new_lead


def _make_lead(raw_text: str = "Wij zijn een fietsenmaker in Antwerpen.") -> dict:
    lead = new_lead(
        search_term="fietsenmaker",
        place_id="p1",
        company_name="Fietsen Peeters",
        website="https://peeters-fietsen.be",
        phone="03 111 22 33",
        source="Google Places",
        fetched_at="2026-06-18T10:00:00",
    )
    lead["raw_text"] = raw_text
    return lead


class FakeLLM:
    def __init__(self, response: AnalysisResult | Exception):
        self.response = response
        self.calls = 0

    async def ainvoke(self, messages):
        self.calls += 1
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


@pytest.mark.asyncio
async def test_analyze_leads_marks_valid_result_ok():
    lead = _make_lead()
    fake = FakeLLM(
        AnalysisResult(
            niche="fietsenmaker gespecialiseerd in elektrische fietsen",
            icebreaker="Ik zag dat jullie gespecialiseerd zijn in e-bike-herstellingen.",
            is_valid=True,
        )
    )
    result = await analyze_leads([lead], llm=fake)
    assert result[0]["status"] == "ok"
    assert result[0]["niche"] == "fietsenmaker gespecialiseerd in elektrische fietsen"
    assert fake.calls == 1


@pytest.mark.asyncio
async def test_analyze_leads_rejects_bad_data():
    lead = _make_lead()
    fake = FakeLLM(AnalysisResult(is_valid=False, reject_reason="Cookie-muur, geen inhoud."))
    result = await analyze_leads([lead], llm=fake)
    assert result[0]["status"] == "skipped"
    assert result[0]["error_reason"] == "Cookie-muur, geen inhoud."


@pytest.mark.asyncio
async def test_analyze_leads_handles_llm_exception_gracefully():
    lead = _make_lead()
    fake = FakeLLM(RuntimeError("API down"))
    result = await analyze_leads([lead], llm=fake)
    assert result[0]["status"] == "error"
    assert "API down" in result[0]["error_reason"]


@pytest.mark.asyncio
async def test_analyze_leads_skips_empty_scrape_text_without_calling_llm():
    lead = _make_lead(raw_text="")
    fake = FakeLLM(AnalysisResult(is_valid=True, niche="x", icebreaker="y"))
    result = await analyze_leads([lead], llm=fake)
    assert result[0]["status"] == "error"
    assert fake.calls == 0


@pytest.mark.asyncio
async def test_analyze_leads_does_not_crash_run_on_one_bad_lead():
    bad_lead = _make_lead(raw_text="")
    good_lead = _make_lead()
    good_lead["place_id"] = "p2"
    fake = FakeLLM(AnalysisResult(is_valid=True, niche="fietsenmaker", icebreaker="..."))
    result = await analyze_leads([bad_lead, good_lead], llm=fake)
    assert result[0]["status"] == "error"
    assert result[1]["status"] == "ok"
