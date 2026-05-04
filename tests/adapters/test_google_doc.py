"""google_doc adapter — Doc by ID → markdown via Drive's export endpoint.

Tests use a hand-rolled fake Drive service rather than recording real
API responses, because the contract is small (two method calls,
``files().get()`` + ``files().export()``) and the network shape is
googleapiclient-specific. Recording cassettes would be heavier than
the methods being tested.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from engine.adapters.google_doc.extractor import (
    GoogleDocExtractError,
    extract_doc_id,
    extract_google_doc,
)

# ─── fake Drive service ─────────────────────────────────────────────


@dataclass
class _FakeRequest:
    """Mimics googleapiclient's Request — has ``.execute()``."""

    payload: Any

    def execute(self) -> Any:
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


@dataclass
class _FakeFiles:
    """Mimics service.files() — get() and export() return _FakeRequest."""

    metadata_payload: Any
    export_payload: Any

    def get(self, *, fileId: str, fields: str) -> _FakeRequest:
        # Surface the call args via class attributes for assertions.
        self._last_get_args = {"fileId": fileId, "fields": fields}
        return _FakeRequest(self.metadata_payload)

    def export(self, *, fileId: str, mimeType: str) -> _FakeRequest:
        self._last_export_args = {"fileId": fileId, "mimeType": mimeType}
        return _FakeRequest(self.export_payload)


class _FakeService:
    """Mimics the Drive v3 service object the production code receives."""

    def __init__(self, *, metadata: Any, export: Any):
        self._files = _FakeFiles(metadata_payload=metadata, export_payload=export)

    def files(self) -> _FakeFiles:
        return self._files


# ─── extract_doc_id ─────────────────────────────────────────────────


def test_extract_doc_id_from_full_url() -> None:
    url = "https://docs.google.com/document/d/1AbCdEfGhIjKlMnOpQrStUv/edit"
    assert extract_doc_id(url) == "1AbCdEfGhIjKlMnOpQrStUv"


def test_extract_doc_id_from_url_without_trailing_path() -> None:
    url = "https://docs.google.com/document/d/1AbCdEfGhIjKlMnOpQrStUv"
    assert extract_doc_id(url) == "1AbCdEfGhIjKlMnOpQrStUv"


def test_extract_doc_id_from_bare_id() -> None:
    bare = "1AbCdEfGhIjKlMnOpQrStUv"
    assert extract_doc_id(bare) == bare


def test_extract_doc_id_rejects_non_doc_url() -> None:
    with pytest.raises(GoogleDocExtractError):
        extract_doc_id("https://docs.google.com/spreadsheets/d/1abc")


def test_extract_doc_id_rejects_garbage() -> None:
    with pytest.raises(GoogleDocExtractError):
        extract_doc_id("not a url and not an id")


# ─── extract_google_doc ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_extract_google_doc_happy_path() -> None:
    """Valid metadata + valid markdown export → ExtractedContent with body+title."""
    service = _FakeService(
        metadata={
            "id": "1AbCdEfGhIjKlMnOpQrStUv",
            "name": "Apollo Q2 Launch Notes",
            "modifiedTime": "2026-04-15T10:00:00Z",
            "mimeType": "application/vnd.google-apps.document",
        },
        export=b"# Apollo Q2\n\nLaunch is on track for 2026-05-15.\n",
    )

    result = await extract_google_doc(
        "https://docs.google.com/document/d/1AbCdEfGhIjKlMnOpQrStUv/edit",
        service=service,
    )

    assert result.failure_reason is None
    assert result.extraction_method == "text"
    assert "Apollo Q2 Launch Notes" in result.text  # title from metadata
    assert "Apollo Q2" in result.text  # body content from export
    assert "2026-04-15T10:00:00Z" in result.text  # last-modified line
    assert "1AbCdEfGhIjKlMnOpQrStUv" in result.text  # source link


@pytest.mark.asyncio
async def test_extract_google_doc_rejects_non_doc_mime() -> None:
    """A Drive file that's not a Google Doc → graceful failure (Sheets/Slides need different export paths)."""
    service = _FakeService(
        metadata={
            "id": "1abc",
            "name": "Q2 budget",
            "mimeType": "application/vnd.google-apps.spreadsheet",
        },
        export=b"",
    )
    result = await extract_google_doc("1abcdefghijklmnopqrstuv", service=service)
    assert result.failure_reason is not None
    assert "not_a_google_doc" in result.failure_reason


@pytest.mark.asyncio
async def test_extract_google_doc_handles_metadata_error() -> None:
    """File-not-found / permission denied bubbles up as a graceful failure."""
    service = _FakeService(
        metadata=PermissionError("403: User does not have access"),
        export=b"",
    )
    result = await extract_google_doc("1abcdefghijklmnopqrstuv", service=service)
    assert result.failure_reason is not None
    assert "metadata_fetch_failed" in result.failure_reason


@pytest.mark.asyncio
async def test_extract_google_doc_handles_empty_export() -> None:
    """An empty doc body → 'empty_doc' failure rather than a stub markdown."""
    service = _FakeService(
        metadata={
            "id": "1abc",
            "name": "Empty",
            "mimeType": "application/vnd.google-apps.document",
        },
        export=b"   \n  \n",
    )
    result = await extract_google_doc("1abcdefghijklmnopqrstuv", service=service)
    assert result.failure_reason is not None
    assert "empty_doc" in result.failure_reason


@pytest.mark.asyncio
async def test_extract_google_doc_invalid_input_does_not_call_api() -> None:
    """An obvious garbage input never reaches the Drive service."""
    service = _FakeService(
        metadata=AssertionError("should not be called"),
        export=AssertionError("should not be called"),
    )
    result = await extract_google_doc("not-a-url-or-id", service=service)
    assert result.failure_reason is not None
    assert "invalid_doc_id" in result.failure_reason


@pytest.mark.asyncio
async def test_dispatch_routes_docs_url_to_google_doc(monkeypatch) -> None:
    """A docs.google.com URL routes to extract_google_doc, not to extract_web."""
    from engine.adapters._template.contract import ExtractedContent
    from engine.utils.dispatch import extract_url

    captured: list[str] = []

    async def _fake_doc(url: str, **_kw):
        captured.append(url)
        return ExtractedContent(text="ok", extraction_method="text")

    async def _fail_web(url: str, **_kw):
        raise AssertionError(f"web fallback called with {url!r}")

    import engine.utils.dispatch as dispatch_module

    monkeypatch.setattr(dispatch_module, "extract_google_doc", _fake_doc)
    monkeypatch.setattr(dispatch_module, "extract_web", _fail_web)

    url = "https://docs.google.com/document/d/1AbCdEfGhIjKlMnOpQrStUv/edit"
    result = await extract_url(url)
    assert result.text == "ok"
    assert captured == [url]
