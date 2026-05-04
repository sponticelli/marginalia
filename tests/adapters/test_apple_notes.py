"""apple_notes adapter — JXA → markdown.

Tests use the ``runner`` injection point to substitute hand-rolled
JSON payloads for the osascript subprocess. This means the test
suite runs everywhere (Linux CI, macOS dev) — the platform check is
its own dedicated test that runs only on macOS via the live path.
"""

from __future__ import annotations

import json

import pytest

from engine.adapters.apple_notes.extractor import (
    extract_apple_note,
    html_to_markdown,
)

# ─── html_to_markdown ───────────────────────────────────────────────


def test_html_to_markdown_handles_headings_and_paragraphs() -> None:
    html = "<h1>Apollo Q2</h1><p>Ship 2026-05-15.</p><h2>Owner</h2><p>Sandro</p>"
    md = html_to_markdown(html)
    assert "# Apollo Q2" in md
    assert "## Owner" in md
    assert "Ship 2026-05-15." in md
    assert "Sandro" in md


def test_html_to_markdown_handles_emphasis() -> None:
    html = "<p>This is <strong>bold</strong> and <em>italic</em>.</p>"
    md = html_to_markdown(html)
    assert "**bold**" in md
    assert "*italic*" in md


def test_html_to_markdown_handles_links() -> None:
    html = '<p>See <a href="https://example.com">the doc</a>.</p>'
    md = html_to_markdown(html)
    assert "[the doc](https://example.com)" in md


def test_html_to_markdown_handles_lists() -> None:
    html = "<ul><li>one</li><li>two</li></ul>"
    md = html_to_markdown(html)
    assert "- one" in md
    assert "- two" in md


def test_html_to_markdown_strips_unknown_tags() -> None:
    """Spans, divs, etc. get stripped without losing the content inside."""
    html = '<div><span style="color:red">important</span></div>'
    md = html_to_markdown(html)
    assert md == "important"


def test_html_to_markdown_decodes_common_entities() -> None:
    html = "<p>5 &gt; 3 &amp; 4 &lt; 9 with&nbsp;space</p>"
    md = html_to_markdown(html)
    assert "5 > 3 & 4 < 9" in md


# ─── extract_apple_note ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_extract_apple_note_renders_note() -> None:
    """Happy path: runner returns canned JSON → markdown body with title + source."""

    def _runner(script, *args, timeout_s):
        return json.dumps(
            {
                "ok": True,
                "title": "Apollo Q2 launch",
                "body": "<h1>Apollo Q2</h1><p>Shipping <strong>2026-05-15</strong>.</p>",
                "modificationDate": "Mon Apr 15 10:00:00 2026",
            }
        )

    result = await extract_apple_note("Apollo Q2 launch", runner=_runner)
    assert result.failure_reason is None
    assert "# Apollo Q2 launch" in result.text  # adapter prepends its own H1
    assert "**2026-05-15**" in result.text
    assert "Source: Apple Notes" in result.text
    assert "Modified: Mon Apr 15" in result.text


@pytest.mark.asyncio
async def test_extract_apple_note_accepts_notes_url_scheme() -> None:
    """``notes://<title>`` form strips the scheme and passes the bare title."""
    captured: list[str] = []

    def _runner(script, *args, timeout_s):
        captured.append(args[0])
        return json.dumps({"ok": True, "title": "X", "body": "<p>body</p>"})

    await extract_apple_note("notes://Q2 launch", runner=_runner)
    assert captured == ["Q2 launch"]


@pytest.mark.asyncio
async def test_extract_apple_note_handles_not_found() -> None:
    """JXA returns {error: 'not_found'} → graceful failure."""

    def _runner(script, *args, timeout_s):
        return json.dumps({"error": "not_found", "title": args[0]})

    result = await extract_apple_note("Nonexistent", runner=_runner)
    assert result.failure_reason is not None
    assert "not_found" in result.failure_reason
    assert "Nonexistent" in result.failure_reason


@pytest.mark.asyncio
async def test_extract_apple_note_handles_osascript_failure() -> None:
    """osascript returning non-zero → osascript_failed graceful failure."""

    def _runner(script, *args, timeout_s):
        raise RuntimeError("osascript failed (exit 1): permission denied")

    result = await extract_apple_note("Anything", runner=_runner)
    assert "osascript_failed" in (result.failure_reason or "")


@pytest.mark.asyncio
async def test_extract_apple_note_handles_bad_json() -> None:
    """Stdout that isn't JSON → parse_failed graceful failure."""

    def _runner(script, *args, timeout_s):
        return "not json"

    result = await extract_apple_note("X", runner=_runner)
    assert "parse_failed" in (result.failure_reason or "")


@pytest.mark.asyncio
async def test_extract_apple_note_handles_empty_input() -> None:
    """Empty title (after stripping notes:// prefix) → invalid_input failure."""

    def _runner(script, *args, timeout_s):
        raise AssertionError("runner should not be called for empty input")

    result = await extract_apple_note("notes://", runner=_runner)
    assert "invalid_input" in (result.failure_reason or "")


@pytest.mark.asyncio
async def test_extract_apple_note_handles_empty_body() -> None:
    """Note exists but its body is empty → empty_note graceful failure."""

    def _runner(script, *args, timeout_s):
        return json.dumps({"ok": True, "title": "Empty", "body": ""})

    result = await extract_apple_note("Empty", runner=_runner)
    assert result.failure_reason == "empty_note"


@pytest.mark.asyncio
async def test_dispatch_routes_notes_scheme(monkeypatch) -> None:
    """A notes:// URL routes to extract_apple_note."""
    from engine.adapters._template.contract import ExtractedContent
    from engine.utils.dispatch import extract_url

    captured: list[str] = []

    async def _fake(url, **_kw):
        captured.append(url)
        return ExtractedContent(text="ok", extraction_method="text")

    import engine.utils.dispatch as dispatch_module

    monkeypatch.setattr(dispatch_module, "extract_apple_note", _fake)
    result = await extract_url("notes://Apollo Q2 launch")
    assert result.text == "ok"
    assert captured == ["notes://Apollo Q2 launch"]
