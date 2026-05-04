"""slack adapter — thread fetch + per-message rendering with mention resolution."""

from __future__ import annotations

import httpx
import pytest

from engine.adapters.slack.extractor import (
    SlackExtractError,
    extract_slack,
    parse_slack_url,
)

# ─── parse_slack_url ────────────────────────────────────────────────


def test_parse_slack_url_basic_archive() -> None:
    url = "https://acme.slack.com/archives/C012345/p1234567890123456"
    ref = parse_slack_url(url)
    assert ref.channel_id == "C012345"
    # The URL form `p<unix-ts><microseconds>` becomes `<ts>.<micros>` for the API.
    assert ref.thread_ts == "1234567890.123456"


def test_parse_slack_url_with_explicit_thread_ts() -> None:
    url = "https://acme.slack.com/archives/C012345/p1234567890123456?thread_ts=1234567890.123456"
    ref = parse_slack_url(url)
    assert ref.thread_ts == "1234567890.123456"


def test_parse_slack_url_rejects_non_slack() -> None:
    with pytest.raises(SlackExtractError):
        parse_slack_url("https://example.com/foo/bar")


# ─── extract_slack ──────────────────────────────────────────────────


def _make_handler(*, replies_payload: dict, users_by_id: dict, channel_payload: dict):
    """Build an httpx handler dispatching slack API endpoints to canned data."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/conversations.replies"):
            return httpx.Response(200, json=replies_payload)
        if path.endswith("/users.info"):
            uid = request.url.params.get("user", "")
            user = users_by_id.get(uid)
            if not user:
                return httpx.Response(200, json={"ok": False, "error": "user_not_found"})
            return httpx.Response(200, json={"ok": True, "user": user})
        if path.endswith("/conversations.info"):
            return httpx.Response(200, json=channel_payload)
        return httpx.Response(404, json={"ok": False, "error": f"unmocked: {path}"})

    return handler


def _make_client(handler) -> httpx.AsyncClient:
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(
        transport=transport,
        base_url="https://slack.com/api",
        headers={"Authorization": "Bearer test-token"},
    )


@pytest.mark.asyncio
async def test_extract_slack_renders_thread_with_user_resolution() -> None:
    """Thread fetch + user mention resolution → markdown with @display_name."""
    handler = _make_handler(
        replies_payload={
            "ok": True,
            "messages": [
                {
                    "type": "message",
                    "user": "U001",
                    "ts": "1234567890.000001",
                    "text": "Should we ship Apollo Q2? <@U002>",
                },
                {
                    "type": "message",
                    "user": "U002",
                    "ts": "1234567890.000002",
                    "text": "Yes — 2026-05-15 is locked.",
                },
            ],
        },
        users_by_id={
            "U001": {"profile": {"display_name": "alice"}, "name": "alice"},
            "U002": {"profile": {"display_name": "bob"}, "name": "bob"},
        },
        channel_payload={"ok": True, "channel": {"name": "apollo-launch"}},
    )

    async with _make_client(handler) as client:
        result = await extract_slack(
            "https://acme.slack.com/archives/C012345/p1234567890123456",
            token="test-token",
            client=client,
        )

    assert result.failure_reason is None
    assert result.extraction_method == "text"
    assert "Slack thread in #apollo-launch" in result.text
    assert "@alice" in result.text  # sender resolved via users.info
    assert "@bob" in result.text
    assert "2026-05-15 is locked" in result.text
    # Mention <@U002> in body should also have been resolved.
    assert "<@U002>" not in result.text  # raw mention should be substituted
    assert "Messages: 2" in result.text


@pytest.mark.asyncio
async def test_extract_slack_renders_links_and_files() -> None:
    """Slack <url|label> link syntax + file attachments are rendered as markdown."""
    handler = _make_handler(
        replies_payload={
            "ok": True,
            "messages": [
                {
                    "type": "message",
                    "user": "U001",
                    "ts": "1234567890.000001",
                    "text": "See <https://example.com/decision|the decision doc>",
                    "files": [
                        {
                            "name": "diagram.png",
                            "url_private": "https://files.slack.com/diagram.png",
                        },
                    ],
                },
            ],
        },
        users_by_id={"U001": {"profile": {"display_name": "alice"}, "name": "alice"}},
        channel_payload={"ok": True, "channel": {"name": "general"}},
    )

    async with _make_client(handler) as client:
        result = await extract_slack(
            "https://acme.slack.com/archives/C012345/p1234567890123456",
            token="t",
            client=client,
        )

    assert "[the decision doc](https://example.com/decision)" in result.text
    assert "📎 [diagram.png](https://files.slack.com/diagram.png)" in result.text


@pytest.mark.asyncio
async def test_extract_slack_handles_invalid_url() -> None:
    """Non-slack URL → invalid_url failure, no API call attempted."""
    result = await extract_slack("https://example.com/foo", token="t")
    assert result.failure_reason is not None
    assert "invalid_url" in result.failure_reason


@pytest.mark.asyncio
async def test_extract_slack_handles_missing_token() -> None:
    """No token + no SLACK_BOT_TOKEN env var → auth_failed."""
    import os

    saved = os.environ.pop("SLACK_BOT_TOKEN", None)
    try:
        result = await extract_slack("https://acme.slack.com/archives/C012345/p1234567890123456")
    finally:
        if saved is not None:
            os.environ["SLACK_BOT_TOKEN"] = saved
    assert "auth_failed" in (result.failure_reason or "")
    assert "SLACK_BOT_TOKEN" in (result.failure_reason or "")


@pytest.mark.asyncio
async def test_extract_slack_handles_api_error() -> None:
    """Slack returns ok=false → graceful failure with the slack error code."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "error": "channel_not_found"})

    async with _make_client(handler) as client:
        result = await extract_slack(
            "https://acme.slack.com/archives/C012345/p1234567890123456",
            token="t",
            client=client,
        )

    assert result.failure_reason is not None
    assert "thread_fetch_failed" in result.failure_reason
    assert "channel_not_found" in result.failure_reason


@pytest.mark.asyncio
async def test_extract_slack_handles_empty_thread() -> None:
    """A thread with zero messages → empty_thread failure."""
    handler = _make_handler(
        replies_payload={"ok": True, "messages": []},
        users_by_id={},
        channel_payload={"ok": True, "channel": {"name": "general"}},
    )

    async with _make_client(handler) as client:
        result = await extract_slack(
            "https://acme.slack.com/archives/C012345/p1234567890123456",
            token="t",
            client=client,
        )

    assert result.failure_reason == "empty_thread"


@pytest.mark.asyncio
async def test_dispatch_routes_slack_url(monkeypatch) -> None:
    """A *.slack.com/archives/... URL routes to extract_slack."""
    from engine.adapters._template.contract import ExtractedContent
    from engine.utils.dispatch import extract_url

    captured: list[str] = []

    async def _fake_slack(url, **_kw):
        captured.append(url)
        return ExtractedContent(text="ok", extraction_method="text")

    async def _fail_web(url, **_kw):
        raise AssertionError("web fallback called")

    import engine.utils.dispatch as dispatch_module

    monkeypatch.setattr(dispatch_module, "extract_slack", _fake_slack)
    monkeypatch.setattr(dispatch_module, "extract_web", _fail_web)

    url = "https://acme.slack.com/archives/C012345/p1234567890123456"
    result = await extract_url(url)
    assert result.text == "ok"
    assert captured == [url]
