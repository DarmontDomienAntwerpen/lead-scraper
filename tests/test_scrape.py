from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from src.nodes.scrape import (
    NEVERBOUNCE_URL,
    _clean_text,
    _extract_email,
    _find_contact_link,
    _find_email,
    _robots_allows,
    _scrape_one,
    _verify_email,
)
from src.state import new_lead


def _make_lead(website: str = "https://example.test") -> dict:
    return new_lead(
        search_term="test",
        place_id="p1",
        company_name="Testbedrijf",
        website=website,
        phone=None,
        source="Google Places",
        fetched_at="2026-06-18T10:00:00",
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
    assert "Timeout" in lead["error_reason"]
    context.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_scrape_one_respects_robots_disallow():
    lead = _make_lead()
    browser = AsyncMock()

    with patch("src.nodes.scrape._robots_allows", AsyncMock(return_value=False)):
        await _scrape_one(browser, lead)

    assert lead["status"] == "error"
    assert "robots.txt" in lead["error_reason"]
    browser.new_context.assert_not_called()


@pytest.mark.asyncio
async def test_scrape_one_success_fills_raw_text():
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
    assert lead["raw_text"] == "Welkom bij Testbedrijf."
    assert lead["email"] is None
    assert lead["email_status"] is None
    assert lead["contact_url"] is None


@pytest.mark.asyncio
async def test_scrape_one_rejects_invalid_email_and_falls_back_to_contact_url():
    lead = _make_lead(website="https://example.test")
    page = AsyncMock()
    page.content.return_value = (
        '<a href="mailto:nep@example.test">Mail</a><a href="/contact">Contact</a>'
    )
    context = AsyncMock()
    context.new_page.return_value = page
    browser = AsyncMock()
    browser.new_context.return_value = context

    with patch("src.nodes.scrape._robots_allows", AsyncMock(return_value=True)), \
         patch("src.nodes.scrape._verify_email", AsyncMock(return_value="invalid")):
        await _scrape_one(browser, lead)

    assert lead["email"] is None
    assert lead["email_status"] == "invalid"
    assert lead["contact_url"] == "https://example.test/contact"


@pytest.mark.asyncio
async def test_scrape_one_keeps_valid_email():
    lead = _make_lead(website="https://example.test")
    page = AsyncMock()
    page.content.return_value = '<a href="mailto:info@example.test">Mail</a>'
    context = AsyncMock()
    context.new_page.return_value = page
    browser = AsyncMock()
    browser.new_context.return_value = context

    with patch("src.nodes.scrape._robots_allows", AsyncMock(return_value=True)), \
         patch("src.nodes.scrape._verify_email", AsyncMock(return_value="valid")):
        await _scrape_one(browser, lead)

    assert lead["email"] == "info@example.test"
    assert lead["email_status"] == "valid"
    assert lead["contact_url"] is None


@pytest.mark.asyncio
async def test_verify_email_returns_none_without_api_key():
    with patch("src.nodes.scrape.NEVERBOUNCE_API_KEY", None):
        assert await _verify_email("info@example.test") is None


@pytest.mark.asyncio
@respx.mock
async def test_verify_email_returns_result_on_success():
    respx.get(NEVERBOUNCE_URL).mock(
        return_value=httpx.Response(200, json={"status": "success", "result": "valid"})
    )
    with patch("src.nodes.scrape.NEVERBOUNCE_API_KEY", "test-key"):
        assert await _verify_email("info@example.test") == "valid"


@pytest.mark.asyncio
@respx.mock
async def test_verify_email_returns_none_on_api_error():
    respx.get(NEVERBOUNCE_URL).mock(
        return_value=httpx.Response(200, json={"status": "auth_failure", "message": "bad key"})
    )
    with patch("src.nodes.scrape.NEVERBOUNCE_API_KEY", "test-key"):
        assert await _verify_email("info@example.test") is None


@pytest.mark.asyncio
@respx.mock
async def test_verify_email_returns_none_on_http_error():
    respx.get(NEVERBOUNCE_URL).mock(return_value=httpx.Response(500))
    with patch("src.nodes.scrape.NEVERBOUNCE_API_KEY", "test-key"):
        assert await _verify_email("info@example.test") is None


@pytest.mark.asyncio
async def test_scrape_one_sets_contact_url_when_no_email_found():
    lead = _make_lead(website="https://example.test")
    page = AsyncMock()
    page.content.return_value = '<a href="/contact">Contact</a><p>Geen e-mail hier.</p>'
    context = AsyncMock()
    context.new_page.return_value = page
    browser = AsyncMock()
    browser.new_context.return_value = context

    with patch("src.nodes.scrape._robots_allows", AsyncMock(return_value=True)):
        await _scrape_one(browser, lead)

    assert lead["email"] is None
    assert lead["contact_url"] == "https://example.test/contact"


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


def test_extract_email_decodes_percent_encoded_mailto():
    html = '<a href="mailto:%69nfo@s%67-p%72otect.be">Mail</a>'
    assert _extract_email(html) == "info@sg-protect.be"


def test_extract_email_strips_doubled_mailto_prefix():
    html = '<a href="mailto:mailto:info@slotenmakerinantwerpen.be">Mail</a>'
    assert _extract_email(html) == "info@slotenmakerinantwerpen.be"


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


