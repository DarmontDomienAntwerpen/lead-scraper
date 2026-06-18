from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from src.nodes.scrape import _clean_text, _extract_email, _find_contact_link, _find_email, _robots_allows, _scrape_one
from src.state import new_lead


def _make_lead(website: str = "https://example.test") -> dict:
    return new_lead(
        zoekterm="test",
        place_id="p1",
        bedrijfsnaam="Testbedrijf",
        website=website,
        telefoon=None,
        bron="Google Places",
        opgehaald_op="2026-06-18T10:00:00",
    )


def test_clean_text_strips_boilerplate_and_caps_length():
    html = (
        "<html><head><style>.x{color:red}</style></head><body>"
        "<nav>Menu item</nav><header>Header</header>"
        "<p>Belangrijke   inhoud   hier.</p>"
        "<footer>Footer info</footer><script>evil()</script>"
        "</body></html>"
    )
    result = _clean_text(html)
    assert "Menu item" not in result
    assert "Header" not in result
    assert "Footer info" not in result
    assert "evil" not in result
    assert "Belangrijke inhoud hier." in result


def test_clean_text_caps_at_max_chars():
    html = "<p>" + ("a" * 10_000) + "</p>"
    result = _clean_text(html)
    assert len(result) == 6000


@pytest.mark.asyncio
async def test_scrape_one_handles_timeout_gracefully():
    lead = _make_lead()
    page = AsyncMock()
    page.goto.side_effect = PlaywrightTimeoutError("timeout")
    context = AsyncMock()
    context.new_page.return_value = page
    browser = AsyncMock()
    browser.new_context.return_value = context

    with patch("src.nodes.scrape._robots_allows", AsyncMock(return_value=True)):
        await _scrape_one(browser, lead)

    assert lead["status"] == "error"
    assert "Timeout" in lead["error_reden"]
    context.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_scrape_one_respects_robots_disallow():
    lead = _make_lead()
    browser = AsyncMock()

    with patch("src.nodes.scrape._robots_allows", AsyncMock(return_value=False)):
        await _scrape_one(browser, lead)

    assert lead["status"] == "error"
    assert "robots.txt" in lead["error_reden"]
    browser.new_context.assert_not_called()


@pytest.mark.asyncio
async def test_scrape_one_success_fills_ruwe_tekst():
    lead = _make_lead()
    page = AsyncMock()
    page.content.return_value = "<p>Welkom bij Testbedrijf.</p>"
    context = AsyncMock()
    context.new_page.return_value = page
    browser = AsyncMock()
    browser.new_context.return_value = context

    with patch("src.nodes.scrape._robots_allows", AsyncMock(return_value=True)):
        await _scrape_one(browser, lead)

    assert lead["status"] == "pending"
    assert lead["ruwe_tekst"] == "Welkom bij Testbedrijf."
    assert lead["email"] is None


def test_extract_email_prefers_mailto_link():
    html = '<a href="mailto:info@peeters-fietsen.be?subject=Hallo">Mail ons</a><p>info@andersadres.be</p>'
    assert _extract_email(html) == "info@peeters-fietsen.be"


def test_extract_email_falls_back_to_text_pattern():
    html = "<footer>Bereik ons via info@peeters-fietsen.be of bel ons.</footer>"
    assert _extract_email(html) == "info@peeters-fietsen.be"


def test_extract_email_ignores_image_filenames():
    html = '<img src="logo@2x.png"><p>Geen echt e-mailadres hier.</p>'
    assert _extract_email(html) is None


def test_extract_email_returns_none_when_absent():
    assert _extract_email("<p>Geen contactinfo op deze pagina.</p>") is None


def test_find_contact_link_matches_href_or_text():
    html = '<a href="/over-ons">Over ons</a><a href="/contact-ons">Neem contact op</a>'
    assert _find_contact_link(html, "https://example.test") == "https://example.test/contact-ons"


def test_find_contact_link_returns_none_without_match():
    html = '<a href="/over-ons">Over ons</a>'
    assert _find_contact_link(html, "https://example.test") is None


@pytest.mark.asyncio
async def test_find_email_falls_back_to_contact_page():
    homepage_html = '<a href="/contact">Contact</a>'
    page = AsyncMock()
    page.content.return_value = "<p>Mail ons op info@peeters-fietsen.be</p>"

    email = await _find_email(page, homepage_html, "https://example.test")

    assert email == "info@peeters-fietsen.be"
    page.goto.assert_awaited_once()


@pytest.mark.asyncio
async def test_find_email_returns_none_when_contact_page_fails():
    homepage_html = '<a href="/contact">Contact</a>'
    page = AsyncMock()
    page.goto.side_effect = PlaywrightTimeoutError("timeout")

    email = await _find_email(page, homepage_html, "https://example.test")

    assert email is None


@pytest.mark.asyncio
@respx.mock
async def test_robots_allows_returns_true_when_robots_txt_unreachable():
    respx.get("https://example.test/robots.txt").mock(return_value=httpx.Response(404))
    assert await _robots_allows("https://example.test/") is True


@pytest.mark.asyncio
@respx.mock
async def test_robots_allows_respects_disallow():
    respx.get("https://example.test/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /\n")
    )
    assert await _robots_allows("https://example.test/") is False
