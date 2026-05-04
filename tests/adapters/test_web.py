"""Web adapter — main-content extraction via trafilatura.

The fixture is a hand-written HTML page with the same boilerplate
shape real articles have (nav, footer, byline, body). Tests verify
trafilatura strips the chrome and keeps the body.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.adapters._template.contract import ExtractedContent
from engine.adapters.web.extractor import (
    DEFAULT_USER_AGENT,
    WebFetchError,
    extract_web,
)
from engine.utils.dispatch import UnsupportedUrlError, extract_url

FIXTURE_HTML = (Path(__file__).parent / "fixtures" / "web" / "example_article.html").read_text(
    encoding="utf-8"
)
SAMPLE_URL = "https://example.com/articles/compounding-wiki"


@pytest.mark.asyncio
async def test_extract_web_returns_main_content() -> None:
    """Fixture HTML → markdown that includes the body and excludes the chrome."""
    result = await extract_web(SAMPLE_URL, cached_html=FIXTURE_HTML)

    assert isinstance(result, ExtractedContent)
    assert result.failure_reason is None
    assert result.extraction_method == "text"
    assert result.cost_usd is None
    # Title should land in the body (we prefix it as H1).
    assert "Compounding Wiki" in result.text
    # Source URL line should appear so reviewers can trace provenance.
    assert SAMPLE_URL in result.text
    # Main-content body terms should survive.
    assert "load-bearing" in result.text
    assert "Strict-schema retry" in result.text
    # Footer/nav chrome should be stripped — these terms shouldn't survive.
    assert "Privacy" not in result.text
    assert "All rights reserved" not in result.text


@pytest.mark.asyncio
async def test_extract_web_rejects_non_http_scheme() -> None:
    """ftp://, file://, and other schemes return a graceful failure."""
    result = await extract_web("ftp://example.com/foo.txt")
    assert result.failure_reason is not None
    assert "invalid_url" in result.failure_reason
    assert result.text == ""


@pytest.mark.asyncio
async def test_extract_web_handles_empty_response() -> None:
    """Empty HTML body → 'insufficient_content' failure, never a crash."""
    result = await extract_web(SAMPLE_URL, cached_html="<html><body></body></html>")
    assert result.failure_reason is not None
    assert "insufficient_content" in result.failure_reason
    assert result.text == ""


@pytest.mark.asyncio
async def test_dispatch_routes_unknown_host_to_web(monkeypatch) -> None:
    """A non-YouTube URL falls through to extract_web (the new fallback)."""
    captured_url: list[str] = []

    async def _fake_extract_web(url: str, **_kw):
        captured_url.append(url)
        return ExtractedContent(text="ok", extraction_method="text")

    import engine.utils.dispatch as dispatch_module

    monkeypatch.setattr(dispatch_module, "extract_web", _fake_extract_web)

    result = await extract_url("https://blog.example.com/foo")
    assert result.text == "ok"
    assert captured_url == ["https://blog.example.com/foo"]


@pytest.mark.asyncio
async def test_dispatch_still_rejects_non_http_schemes() -> None:
    """``file://`` / other schemes still raise — web adapter only handles open web."""
    with pytest.raises(UnsupportedUrlError):
        await extract_url("file:///etc/hosts")


def test_default_user_agent_identifies_marginalia() -> None:
    """The UA string includes a project marker so site owners can identify us."""
    assert "marginalia" in DEFAULT_USER_AGENT.lower()


def test_web_fetch_error_is_a_value_error() -> None:
    """WebFetchError extends ValueError so callers can catch broadly if needed."""
    assert issubclass(WebFetchError, ValueError)
