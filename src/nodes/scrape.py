"""Scrape node: async Playwright + BeautifulSoup.

robots.txt: a best-effort check runs before every scrape. On an explicit disallow
the site is not scraped (lead -> status=error). If robots.txt cannot be fetched or
parsed, scraping continues: that is the common convention (an unreachable
robots.txt is not a disallow) and prevents losing leads to a broken robots.txt.

Sites are scraped sequentially with one reused browser and a short, polite pause
(`SCRAPE_DELAY_SECONDS`) between leads: no aggressive parallel requests to small
business websites.

Email address: Google Places does not provide one, so it is looked up on the
homepage (mailto links first, then a regex fallback on the visible text). If
nothing is found, one link that looks like a contact page is followed and searched
as well. This is best-effort: if it fails, `email` stays empty and the lead is kept.

Email verification: every address found is checked with the NeverBounce single-check
API. An `invalid`/`disposable` result removes the address again (falling back to the
contact page URL when there is one), since it would almost certainly bounce.
`valid`/`catchall`/`unknown` is kept. On an API error, or without
`NEVERBOUNCE_API_KEY`, the check is skipped and the address stays unverified.
"""

from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import unquote, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup
from playwright.async_api import Browser, Page, TimeoutError as PlaywrightTimeoutError, async_playwright

from src.config import (
    NEVERBOUNCE_API_KEY,
    SCRAPE_DELAY_SECONDS,
    SCRAPE_MAX_CHARS,
    SCRAPE_TIMEOUT_MS,
    SIGNAL_HTML_MAX_CHARS,
    USER_AGENT,
    VIEWPORT,
)
from src.state import LeadState

logger = logging.getLogger(__name__)

_EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
_NON_EMAIL_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")

NEVERBOUNCE_URL = "https://api.neverbounce.com/v4/single/check"
_REJECTED_EMAIL_STATUSES = {"invalid", "disposable"}


async def scrape_leads(leads: list[LeadState]) -> list[LeadState]:
    """Scrape the homepage of every pending lead in place and return the list."""
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
            lead["error_reason"] = "robots.txt disallows scraping this site"
            logger.info("Skipped (robots.txt): %s", url)
            return

        context = await browser.new_context(user_agent=USER_AGENT, viewport=VIEWPORT)
        try:
            page = await context.new_page()
            await page.goto(url, timeout=SCRAPE_TIMEOUT_MS, wait_until="domcontentloaded")
            html = await page.content()
            lead["raw_text"] = _clean_text(html)
            # Keep the unstripped HTML (already fetched, no extra request) so the signals
            # node can detect structural elements (forms, mailto/tel links, widget scripts)
            # that are stripped from `raw_text`.
            lead["raw_html"] = html[:SIGNAL_HTML_MAX_CHARS]
            email = await _find_email(page, html, url)
            email_status = await _verify_email(email) if email else None
            if email and email_status in _REJECTED_EMAIL_STATUSES:
                logger.info("Email rejected after NeverBounce check (%s): %s", email_status, email)
                email = None
            lead["email"] = email
            lead["email_status"] = email_status
            # No automatic form filling or sending: only pass the URL along, so the user
            # can open the contact form manually when no (verified) email was found.
            lead["contact_url"] = None if email else _find_contact_link(html, url)
        finally:
            await context.close()
    except PlaywrightTimeoutError:
        lead["status"] = "error"
        lead["error_reason"] = f"Timeout while loading {url}"
        logger.warning("Scrape timeout: %s", url)
    except Exception as exc:  # noqa: BLE001 - one failed scrape must not stop the run
        lead["status"] = "error"
        lead["error_reason"] = f"Scrape error: {exc}"
        logger.warning("Scrape error for %s: %s", url, exc)


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
    """Best-effort email lookup: mailto links/text on the homepage, otherwise one try on the contact page."""
    email = _extract_email(homepage_html)
    if email:
        return email

    contact_url = _find_contact_link(homepage_html, base_url)
    if not contact_url or contact_url == base_url:
        return None
    try:
        await page.goto(contact_url, timeout=SCRAPE_TIMEOUT_MS, wait_until="domcontentloaded")
        return _extract_email(await page.content())
    except Exception as exc:  # noqa: BLE001 - must not invalidate the homepage scrape
        logger.debug("No email found on contact page %s: %s", contact_url, exc)
        return None


def _clean_email(addr: str) -> str | None:
    """Normalise a raw extracted address: URL-decode it (some sites obfuscate mailto
    links with percent-encoding, e.g. %69nfo@... for info@...) and strip a doubled
    'mailto:' prefix (some sites wrongly write href="mailto:mailto:address@...").
    Returns None when the result is not a valid email address, instead of passing
    garbage along."""
    addr = unquote(addr.strip())
    while addr.lower().startswith("mailto:"):
        addr = addr[len("mailto:"):]
    addr = addr.strip()
    if _EMAIL_PATTERN.fullmatch(addr):
        return addr
    return None


def _extract_email(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.select('a[href^="mailto:"]'):
        raw = tag["href"].split("mailto:", 1)[1].split("?")[0]
        addr = _clean_email(raw)
        if addr:
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


async def _verify_email(email: str) -> str | None:
    """Ask NeverBounce for the result (valid/invalid/disposable/catchall/unknown).

    Returns None without a key, on an API error or on an unexpected response; the
    address then simply stays unverified."""
    if not NEVERBOUNCE_API_KEY:
        return None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                NEVERBOUNCE_URL, params={"key": NEVERBOUNCE_API_KEY, "email": email}
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        logger.warning("NeverBounce HTTP error for %s: %s", email, exc)
        return None

    if data.get("status") != "success":
        logger.warning("NeverBounce error for %s: %s", email, data.get("message"))
        return None
    return data.get("result")
