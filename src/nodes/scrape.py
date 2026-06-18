"""ENGINEER: scrape-node — async Playwright + BeautifulSoup.

Robots.txt-keuze: we doen een best-effort check van robots.txt vóór elke
scrape. Bij een expliciete disallow scrapen we niet (lead -> status=error).
Als robots.txt niet opgehaald/geparsed kan worden, scrapen we wél door — dat
is de gangbare conventie (een onbereikbaar robots.txt is geen disallow) en
voorkomt dat een kapotte/afwezige robots.txt onnodig leads laat sneuvelen.

We scrapen sequentieel met één herbruikte browser-instance en een korte
beleefde pauze (`SCRAPE_DELAY_SECONDS`) tussen leads — geen agressieve
parallelle requests naar kleine KMO-sites.
"""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup
from playwright.async_api import Browser, TimeoutError as PlaywrightTimeoutError, async_playwright

from src.config import SCRAPE_DELAY_SECONDS, SCRAPE_MAX_CHARS, SCRAPE_TIMEOUT_MS, USER_AGENT, VIEWPORT
from src.state import LeadState

logger = logging.getLogger(__name__)


async def scrape_leads(leads: list[LeadState]) -> list[LeadState]:
    """Scrapet de homepage van elke pending lead in-place en geeft de lijst terug."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            for lead in leads:
                if lead["status"] != "pending" or not lead["website"]:
                    continue
                await _scrape_one(browser, lead)
                await asyncio.sleep(SCRAPE_DELAY_SECONDS)
        finally:
            await browser.close()
    return leads


async def _scrape_one(browser: Browser, lead: LeadState) -> None:
    url = lead["website"]
    try:
        if not await _robots_allows(url):
            lead["status"] = "error"
            lead["error_reden"] = "robots.txt verbiedt scrapen van deze site"
            logger.info("Overgeslagen (robots.txt): %s", url)
            return

        context = await browser.new_context(user_agent=USER_AGENT, viewport=VIEWPORT)
        try:
            page = await context.new_page()
            await page.goto(url, timeout=SCRAPE_TIMEOUT_MS, wait_until="domcontentloaded")
            html = await page.content()
            lead["ruwe_tekst"] = _clean_text(html)
        finally:
            await context.close()
    except PlaywrightTimeoutError:
        lead["status"] = "error"
        lead["error_reden"] = f"Timeout bij laden van {url}"
        logger.warning("Scrape timeout: %s", url)
    except Exception as exc:  # noqa: BLE001 - elke scrape-fout mag de run niet stoppen
        lead["status"] = "error"
        lead["error_reden"] = f"Scrape-fout: {exc}"
        logger.warning("Scrape-fout voor %s: %s", url, exc)


async def _robots_allows(url: str) -> bool:
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(robots_url, headers={"User-Agent": USER_AGENT})
        if resp.status_code >= 400:
            return True
        parser = RobotFileParser()
        parser.parse(resp.text.splitlines())
        return parser.can_fetch(USER_AGENT, url)
    except httpx.HTTPError:
        return True


def _clean_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator=" ")
    text = " ".join(text.split())
    return text[:SCRAPE_MAX_CHARS]
