"""ENGINEER: zoeknode — Google Places Text Search + Place Details.

We gebruiken de REST-endpoints rechtstreeks via httpx (geen `googlemaps`
package nodig). Kostenbewust: Place Details wordt enkel opgevraagd met de
velden die we echt nodig hebben (`website`, `formatted_phone_number`), en we
stoppen zodra we `max_leads` bedrijven MET website hebben. Bedrijven zonder
website worden meteen verworpen (geen scrape-/analyse-kosten op leads die we
toch niet kunnen mailen via hun site).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import httpx

from src.config import GOOGLE_PLACES_API_KEY
from src.state import LeadState, new_lead

logger = logging.getLogger(__name__)

TEXT_SEARCH_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"
DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"
DETAILS_FIELDS = "website,formatted_phone_number"
BRON = "Google Places"
MAX_PAGES = 3  # legacy Text Search geeft max 20 resultaten/pagina -> max 60 candidates


async def search_leads(
    zoekterm: str,
    regio: str,
    max_leads: int,
    client: httpx.AsyncClient | None = None,
) -> list[LeadState]:
    """Zoekt `{zoekterm} {regio}` op Google Places en geeft tot `max_leads` leads MET website terug."""
    if client is not None:
        return await _search_with_client(client, zoekterm, regio, max_leads)
    async with httpx.AsyncClient(timeout=10.0) as owned_client:
        return await _search_with_client(owned_client, zoekterm, regio, max_leads)


async def _search_with_client(
    client: httpx.AsyncClient, zoekterm: str, regio: str, max_leads: int
) -> list[LeadState]:
    query = f"{zoekterm} {regio}"
    leads: list[LeadState] = []
    seen_place_ids: set[str] = set()
    next_page_token: str | None = None
    page = 0

    while len(leads) < max_leads and page < MAX_PAGES:
        if next_page_token:
            # Google vereist een korte vertraging voor een next_page_token geldig wordt.
            await asyncio.sleep(2.0)
            params = {"pagetoken": next_page_token, "key": GOOGLE_PLACES_API_KEY}
        else:
            params = {"query": query, "region": "be", "key": GOOGLE_PLACES_API_KEY}

        data = await _get_json(client, TEXT_SEARCH_URL, params)
        if data is None:
            break

        status = data.get("status")
        if status not in ("OK", "ZERO_RESULTS"):
            logger.error(
                "Google Places text search faalde: %s (%s)",
                status,
                data.get("error_message"),
            )
            break

        for result in data.get("results", []):
            if len(leads) >= max_leads:
                break
            place_id = result.get("place_id")
            name = result.get("name")
            if not place_id or not name or place_id in seen_place_ids:
                continue
            seen_place_ids.add(place_id)

            details = await _fetch_details(client, place_id)
            if details is None:
                continue
            website = details.get("website")
            if not website:
                logger.debug("Lead overgeslagen (geen website): %s", name)
                continue

            leads.append(
                new_lead(
                    zoekterm=zoekterm,
                    place_id=place_id,
                    bedrijfsnaam=name,
                    website=website,
                    telefoon=details.get("formatted_phone_number"),
                    bron=BRON,
                    opgehaald_op=datetime.now(timezone.utc).isoformat(),
                )
            )

        next_page_token = data.get("next_page_token")
        page += 1
        if not next_page_token:
            break

    logger.info("Search node: %d leads met website gevonden voor '%s'", len(leads), query)
    return leads


async def _fetch_details(client: httpx.AsyncClient, place_id: str) -> dict | None:
    data = await _get_json(
        client,
        DETAILS_URL,
        {"place_id": place_id, "fields": DETAILS_FIELDS, "key": GOOGLE_PLACES_API_KEY},
    )
    if data is None:
        return None
    if data.get("status") != "OK":
        logger.warning("Place Details faalde voor %s: %s", place_id, data.get("status"))
        return None
    return data.get("result", {})


async def _get_json(client: httpx.AsyncClient, url: str, params: dict) -> dict | None:
    try:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as exc:
        logger.warning("HTTP-fout bij Google Places call (%s): %s", url, exc)
        return None
