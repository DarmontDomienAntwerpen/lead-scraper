"""ENGINEER: scrape-node — async Playwright + BeautifulSoup.

Robots.txt-keuze: we doen een best-effort check van robots.txt vóór elke
scrape. Bij een expliciete disallow scrapen we niet (lead -> status=error).
Als robots.txt niet opgehaald/geparsed kan worden, scrapen we wél door — dat
is de gangbare conventie (een onbereikbaar robots.txt is geen disallow) en
voorkomt dat een kapotte/afwezige robots.txt onnodig leads laat sneuvelen.

We scrapen sequentieel met één herbruikte browser-instance en een korte
beleefde pauze (`SCRAPE_DELAY_SECONDS`) tussen leads — geen agressieve
parallelle requests naar kleine KMO-sites.

E-mailadres: Google Places levert geen e-mailadres, dus we zoeken het zelf op
de homepage (mailto-links, dan een regex-fallback in de zichtbare tekst). Is
er niets gevonden, dan volgen we één link die naar een contactpagina lijkt te
verwijzen en proberen we daar opnieuw — best-effort, faalt die stap dan blijft
`email` leeg zonder de lead te laten mislukken.
"""

from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup
from playwright.async_api import Browser, Page, TimeoutError as PlaywrightTimeoutError, async_playwright

from src.config import SCRAPE_DELAY_SECONDS, SCRAPE_MAX_CHARS, SCRAPE_TIMEOUT_MS, USER_AGENT, VIEWPORT
from src.state import LeadState

logger = logging.getLogger(__name__)

_EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
_NON_EMAIL_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")


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
            lead["email"] = await _find_email(page, html, url)
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


async def _find_email(page: Page, homepage_html: str, base_url: str) -> str | None:
    """Best-effort e-mailzoektocht: mailto-links/tekst op de homepage, anders één poging op de contactpagina."""
    email = _extract_email(homepage_html)
    if email:
        return email

    contact_url = _find_contact_link(homepage_html, base_url)
    if not contact_url or contact_url == base_url:
        return None
    try:
        await page.goto(contact_url, timeout=SCRAPE_TIMEOUT_MS, wait_until="domcontentloaded")
        return _extract_email(await page.content())
    except Exception as exc:  # noqa: BLE001 - dit mag de scrape van de homepage niet ongeldig maken
        logger.debug("Geen e-mail gevonden op contactpagina %s: %s", contact_url, exc)
        return None


def _extract_email(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.select('a[href^="mailto:"]'):
        addr = tag["href"].split("mailto:", 1)[1].split("?")[0].strip()
        if addr and "@" in addr:
            return addr

    text = soup.get_text(separator=" ")
    for match in _EMAIL_PATTERN.findall(text):
        if not match.lower().endswith(_NON_EMAIL_EXTENSIONS):
            return match
    return None


def _find_contact_link(html: str, base_url: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all("a", href=True):
        href = tag["href"]
        text = tag.get_text(strip=True).lower()
        if "contact" in href.lower() or "contact" in text:
            return urljoin(base_url, href)
    return None
