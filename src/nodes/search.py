"""Search node: Google Places Text Search + Place Details.

Calls the REST endpoints directly with httpx (no `googlemaps` package needed).
Cost-aware: Place Details only requests the fields we actually use (`website`,
`formatted_phone_number`, `reviews`), and the search stops as soon as `max_leads`
businesses WITH a website are found. Businesses without a website are dropped
immediately, so no scrape or analysis cost is spent on them.
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
DETAILS_FIELDS = "website,formatted_phone_number,reviews"
SOURCE = "Google Places"
MAX_PAGES = 3  # legacy Text Search returns max 20 results/page -> max 60 candidates


async def search_leads(
    search_term: str,
    region: str,
    max_leads: int,
    client: httpx.AsyncClient | None = None,
) -> list[LeadState]:
    """Search `{search_term} {region}` on Google Places and return up to `max_leads` leads WITH a website."""
    if client is not None:
        return await _search_with_client(client, search_term, region, max_leads)
    async with httpx.AsyncClient(timeout=10.0) as owned_client:
        return await _search_with_client(owned_client, search_term, region, max_leads)


async def _search_with_client(
    client: httpx.AsyncClient, search_term: str, region: str, max_leads: int
) -> list[LeadState]:
    query = f"{search_term} {region}"
    leads: list[LeadState] = []
    seen_place_ids: set[str] = set()
    next_page_token: str | None = None
    page = 0

    while len(leads) < max_leads and page < MAX_PAGES:
        if next_page_token:
            # Google needs a short delay before a next_page_token becomes valid.
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
                "Google Places text search failed: %s (%s)",
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
                logger.debug("Lead skipped (no website): %s", name)
                continue

            reviews_text = " ".join(
                r.get("text", "") for r in details.get("reviews", []) if r.get("text")
            )
            lead = new_lead(
                search_term=search_term,
                place_id=place_id,
                company_name=name,
                website=website,
                phone=details.get("formatted_phone_number"),
                source=SOURCE,
                fetched_at=datetime.now(timezone.utc).isoformat(),
            )
            lead["reviews_text"] = reviews_text
            leads.append(lead)

        next_page_token = data.get("next_page_token")
        page += 1
        if not next_page_token:
            break

    logger.info("Search node: found %d leads with a website for '%s'", len(leads), query)
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
        logger.warning("Place Details failed for %s: %s", place_id, data.get("status"))
        return None
    return data.get("result", {})


async def _get_json(client: httpx.AsyncClient, url: str, params: dict) -> dict | None:
    try:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as exc:
        logger.warning("HTTP error on Google Places call (%s): %s", url, exc)
        return None
