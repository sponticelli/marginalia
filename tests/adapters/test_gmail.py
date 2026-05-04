"""gmail adapter — thread fetch + per-message markdown rendering.

Real Gmail responses are deeply nested (multipart/alternative inside
multipart/mixed inside the thread); the fakes below mimic that
structure so the tree-walking logic actually gets exercised.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

import pytest

from engine.adapters.gmail.extractor import (
    GmailExtractError,
    extract_gmail,
    extract_gmail_id,
)

# ─── helpers for building fake Gmail message payloads ───────────────


def _b64(s: str) -> str:
    """Gmail base64url-encodes message bodies."""
    return base64.urlsafe_b64encode(s.encode("utf-8")).decode("ascii").rstrip("=")


def _plaintext_message(
    *, sender: str, date: str, subject: str, body: str, message_id: str = "m1"
) -> dict:
    """Build one Gmail Message dict with a single text/plain part."""
    return {
        "id": message_id,
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": sender},
                {"name": "Date", "value": date},
                {"name": "Subject", "value": subject},
            ],
            "body": {"data": _b64(body)},
        },
    }


def _multipart_message(
    *,
    sender: str,
    date: str,
    subject: str,
    plain: str,
    html: str,
    message_id: str = "m2",
) -> dict:
    """Build a multipart/alternative message — both text and HTML parts."""
    return {
        "id": message_id,
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [
                {"name": "From", "value": sender},
                {"name": "Date", "value": date},
                {"name": "Subject", "value": subject},
            ],
            "parts": [
                {"mimeType": "text/plain", "body": {"data": _b64(plain)}},
                {"mimeType": "text/html", "body": {"data": _b64(html)}},
            ],
        },
    }


# ─── fake Gmail service ─────────────────────────────────────────────


@dataclass
class _FakeRequest:
    payload: Any

    def execute(self) -> Any:
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class _FakeThreads:
    def __init__(self, payload: Any):
        self._payload = payload

    def get(self, *, userId: str, id: str, format: str) -> _FakeRequest:
        self.last_args = {"userId": userId, "id": id, "format": format}
        return _FakeRequest(self._payload)


class _FakeUsers:
    def __init__(self, threads_payload: Any):
        self._threads = _FakeThreads(threads_payload)

    def threads(self) -> _FakeThreads:
        return self._threads


class _FakeService:
    def __init__(self, *, threads: Any):
        self._users = _FakeUsers(threads)

    def users(self) -> _FakeUsers:
        return self._users


# ─── extract_gmail_id ───────────────────────────────────────────────


def test_extract_gmail_id_from_url() -> None:
    url = "https://mail.google.com/mail/u/0/#inbox/abcd1234ef567890"
    assert extract_gmail_id(url) == "abcd1234ef567890"


def test_extract_gmail_id_from_search_url() -> None:
    url = "https://mail.google.com/mail/u/0/#search/q2/abcd1234ef567890"
    assert extract_gmail_id(url) == "abcd1234ef567890"


def test_extract_gmail_id_from_bare_id() -> None:
    bare = "abcd1234ef567890"
    assert extract_gmail_id(bare) == bare


def test_extract_gmail_id_rejects_short_string() -> None:
    with pytest.raises(GmailExtractError):
        extract_gmail_id("short")


# ─── extract_gmail ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_extract_gmail_renders_single_message_thread() -> None:
    """One-message thread with plaintext body → markdown body + thread metadata."""
    thread = {
        "messages": [
            _plaintext_message(
                sender="alice@example.com",
                date="Mon, 15 Apr 2026 10:00:00 +0000",
                subject="Apollo Q2 launch date",
                body="Confirming we ship 2026-05-15.\n\n— Alice",
            )
        ]
    }
    service = _FakeService(threads=thread)

    result = await extract_gmail("abcd1234ef567890", service=service)

    assert result.failure_reason is None
    assert result.extraction_method == "text"
    assert "Apollo Q2 launch date" in result.text  # subject
    assert "alice@example.com" in result.text
    assert "ship 2026-05-15" in result.text
    assert "Messages: 1" in result.text


@pytest.mark.asyncio
async def test_extract_gmail_handles_multipart_alternative() -> None:
    """multipart/alternative messages: prefer text/plain, ignore the HTML twin."""
    thread = {
        "messages": [
            _multipart_message(
                sender="bob@example.com",
                date="Tue, 16 Apr 2026 09:00:00 +0000",
                subject="Re: Apollo",
                plain="Looks good — approved.",
                html="<p>Looks <b>good</b> — <span>approved.</span></p>",
            )
        ]
    }
    result = await extract_gmail("abcd1234ef567890", service=_FakeService(threads=thread))
    assert "Looks good — approved." in result.text
    # HTML span/b shouldn't appear since text/plain was picked.
    assert "<b>" not in result.text
    assert "<span>" not in result.text


@pytest.mark.asyncio
async def test_extract_gmail_strips_quotes_by_default() -> None:
    """Quoted reply chains (>) and Gmail quote headers are dropped by default."""
    thread = {
        "messages": [
            _plaintext_message(
                sender="bob@example.com",
                date="Tue",
                subject="Re: thing",
                body=(
                    "I agree.\n\n"
                    "On Mon, 15 Apr 2026 at 10:00, alice@example.com wrote:\n"
                    "> What do you think?\n"
                    "> Should we ship?\n"
                ),
            )
        ]
    }
    result = await extract_gmail("abcd1234ef567890", service=_FakeService(threads=thread))
    assert "I agree" in result.text
    # Quote header line should be gone.
    assert "On Mon, 15 Apr 2026 at 10:00" not in result.text
    assert "What do you think?" not in result.text


@pytest.mark.asyncio
async def test_extract_gmail_keeps_quotes_when_requested() -> None:
    """include_quotes=True preserves the reply chain."""
    thread = {
        "messages": [
            _plaintext_message(
                sender="bob@example.com",
                date="Tue",
                subject="Re: thing",
                body="I agree.\n\n> What do you think?\n",
            )
        ]
    }
    result = await extract_gmail(
        "abcd1234ef567890", service=_FakeService(threads=thread), include_quotes=True
    )
    assert "What do you think?" in result.text


@pytest.mark.asyncio
async def test_extract_gmail_renders_multi_message_thread() -> None:
    """A thread of N messages: each gets its own header + body block."""
    thread = {
        "messages": [
            _plaintext_message(
                sender="alice@example.com",
                date="Mon",
                subject="Apollo",
                body="When do we ship?",
                message_id="m1",
            ),
            _plaintext_message(
                sender="bob@example.com",
                date="Tue",
                subject="Re: Apollo",
                body="2026-05-15.",
                message_id="m2",
            ),
            _plaintext_message(
                sender="alice@example.com",
                date="Wed",
                subject="Re: Apollo",
                body="Approved.",
                message_id="m3",
            ),
        ]
    }
    result = await extract_gmail("abcd1234ef567890", service=_FakeService(threads=thread))
    assert "When do we ship?" in result.text
    assert "2026-05-15" in result.text
    assert "Approved." in result.text
    assert "Messages: 3" in result.text
    assert result.pages is not None and len(result.pages) == 3


@pytest.mark.asyncio
async def test_extract_gmail_handles_thread_fetch_error() -> None:
    """403 / not-found bubbles up as a graceful failure."""
    service = _FakeService(threads=PermissionError("403: insufficient permissions"))
    result = await extract_gmail("abcd1234ef567890", service=service)
    assert result.failure_reason is not None
    assert "thread_fetch_failed" in result.failure_reason


@pytest.mark.asyncio
async def test_extract_gmail_handles_empty_thread() -> None:
    """Thread with zero messages → empty_thread failure."""
    service = _FakeService(threads={"messages": []})
    result = await extract_gmail("abcd1234ef567890", service=service)
    assert result.failure_reason == "empty_thread"


@pytest.mark.asyncio
async def test_extract_gmail_invalid_input_does_not_call_api() -> None:
    """Garbage input never reaches the Gmail service."""
    service = _FakeService(threads=AssertionError("should not be called"))
    result = await extract_gmail("not-an-id", service=service)
    assert "invalid_thread_id" in (result.failure_reason or "")


@pytest.mark.asyncio
async def test_dispatch_routes_gmail_url(monkeypatch) -> None:
    """A mail.google.com URL routes to extract_gmail, not to extract_web."""
    from engine.adapters._template.contract import ExtractedContent
    from engine.utils.dispatch import extract_url

    captured: list[str] = []

    async def _fake_gmail(url: str, **_kw):
        captured.append(url)
        return ExtractedContent(text="ok", extraction_method="text")

    async def _fail_web(url: str, **_kw):
        raise AssertionError(f"web fallback called with {url!r}")

    import engine.utils.dispatch as dispatch_module

    monkeypatch.setattr(dispatch_module, "extract_gmail", _fake_gmail)
    monkeypatch.setattr(dispatch_module, "extract_web", _fail_web)

    url = "https://mail.google.com/mail/u/0/#inbox/abcd1234ef567890"
    result = await extract_url(url)
    assert result.text == "ok"
    assert captured == [url]
