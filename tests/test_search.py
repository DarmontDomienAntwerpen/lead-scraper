import httpx
import pytest
import respx

from src.nodes.search import DETAILS_URL, TEXT_SEARCH_URL, search_leads


@pytest.mark.asyncio
@respx.mock
async def test_search_leads_filters_no_website_and_dedupes():
    respx.get(TEXT_SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "OK",
                "results": [
                    {"place_id": "p1", "name": "Bakkerij Janssens"},
                    {"place_id": "p2", "name": "Geen Website Shop"},
                    {"place_id": "p1", "name": "Bakkerij Janssens"},  # duplicate
                ],
                "next_page_token": None,
            },
        )
    )

    def details_side_effect(request: httpx.Request) -> httpx.Response:
        place_id = request.url.params.get("place_id")
        if place_id == "p1":
            return httpx.Response(
                200,
                json={
                    "status": "OK",
                    "result": {"website": "https://janssens.be", "formatted_phone_number": "03 123 45 67"},
                },
            )
        return httpx.Response(200, json={"status": "OK", "result": {}})

    respx.get(DETAILS_URL).mock(side_effect=details_side_effect)

    async with httpx.AsyncClient() as client:
        leads = await search_leads("bakker", "Antwerpen", max_leads=20, client=client)

    assert len(leads) == 1
    assert leads[0]["bedrijfsnaam"] == "Bakkerij Janssens"
    assert leads[0]["website"] == "https://janssens.be"
    assert leads[0]["telefoon"] == "03 123 45 67"
    assert leads[0]["status"] == "pending"


@pytest.mark.asyncio
@respx.mock
async def test_search_leads_respects_max_leads():
    respx.get(TEXT_SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "OK",
                "results": [{"place_id": f"p{i}", "name": f"Bedrijf {i}"} for i in range(5)],
                "next_page_token": None,
            },
        )
    )
    respx.get(DETAILS_URL).mock(
        return_value=httpx.Response(
            200, json={"status": "OK", "result": {"website": "https://example.be"}}
        )
    )

    async with httpx.AsyncClient() as client:
        leads = await search_leads("bakker", "Antwerpen", max_leads=2, client=client)

    assert len(leads) == 2


@pytest.mark.asyncio
@respx.mock
async def test_search_leads_stops_gracefully_on_api_error():
    respx.get(TEXT_SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"status": "REQUEST_DENIED", "results": []})
    )

    async with httpx.AsyncClient() as client:
        leads = await search_leads("bakker", "Antwerpen", max_leads=20, client=client)

    assert leads == []
