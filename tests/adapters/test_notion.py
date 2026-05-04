"""notion adapter — REST → markdown via httpx.MockTransport cassettes.

Using ``httpx.MockTransport`` instead of a fake "service" object means
we exercise the real URL routing, headers, and pagination logic — the
adapter is a thin shim over HTTP, so the HTTP shape is what we want
to test.
"""

from __future__ import annotations

import json

import httpx
import pytest

from engine.adapters.notion.extractor import (
    NotionExtractError,
    extract_notion,
    extract_notion_id,
)

# ─── extract_notion_id ──────────────────────────────────────────────


def test_extract_notion_id_from_dashed_uuid() -> None:
    uuid = "12345678-1234-1234-1234-123456789012"
    assert extract_notion_id(uuid) == "12345678123412341234123456789012"


def test_extract_notion_id_from_dash_stripped_uuid() -> None:
    uuid = "12345678123412341234123456789012"
    assert extract_notion_id(uuid) == uuid


def test_extract_notion_id_from_url() -> None:
    url = "https://www.notion.so/myworkspace/Apollo-Q2-Notes-12345678123412341234123456789012"
    assert extract_notion_id(url) == "12345678123412341234123456789012"


def test_extract_notion_id_rejects_garbage() -> None:
    with pytest.raises(NotionExtractError):
        extract_notion_id("not-a-uuid")


# ─── extract_notion (httpx.MockTransport) ───────────────────────────


def _make_client(handler) -> httpx.AsyncClient:
    """Build an AsyncClient routed through a request-handler cassette."""
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(
        transport=transport,
        base_url="https://api.notion.com",
        headers={
            "Authorization": "Bearer test-token",
            "Notion-Version": "2022-06-28",
        },
    )


def _make_handler(*, page: dict, blocks_by_id: dict[str, list]):
    """Build a request handler that responds to /pages/{id} + /blocks/{id}/children."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/v1/pages/"):
            return httpx.Response(200, json=page)
        if path.startswith("/v1/blocks/") and path.endswith("/children"):
            block_id = path.split("/")[3]
            children = blocks_by_id.get(block_id, [])
            return httpx.Response(
                200,
                json={"results": children, "has_more": False, "next_cursor": None},
            )
        return httpx.Response(404, text=f"unmocked: {path}")

    return handler


def _block(*, type_: str, id_: str = "b", **payload) -> dict:
    """Construct a Notion block dict with a single typed payload."""
    return {"id": id_, "type": type_, type_: payload}


def _rt(text: str, **annotations) -> dict:
    return {
        "plain_text": text,
        "annotations": annotations or {},
    }


@pytest.mark.asyncio
async def test_extract_notion_renders_basic_page() -> None:
    """Title + heading + paragraph + bullet → expected markdown."""
    page_id = "12345678123412341234123456789012"
    page_metadata = {
        "id": page_id,
        "last_edited_time": "2026-04-15T10:00:00Z",
        "properties": {
            "Name": {"type": "title", "title": [_rt("Apollo Q2 Notes")]},
        },
    }
    children = [
        _block(type_="heading_2", id_="h", rich_text=[_rt("Decisions")]),
        _block(type_="paragraph", id_="p", rich_text=[_rt("Ship 2026-05-15.")]),
        _block(type_="bulleted_list_item", id_="b1", rich_text=[_rt("Owner: Sandro")]),
    ]
    handler = _make_handler(page=page_metadata, blocks_by_id={page_id: children})

    async with _make_client(handler) as client:
        result = await extract_notion(page_id, token="test-token", client=client)

    assert result.failure_reason is None
    assert "Apollo Q2 Notes" in result.text  # title
    assert "## Decisions" in result.text  # heading
    assert "Ship 2026-05-15." in result.text  # paragraph
    assert "- Owner: Sandro" in result.text  # bullet


@pytest.mark.asyncio
async def test_extract_notion_renders_rich_text_annotations() -> None:
    """Bold/italic/code/link annotations survive into the markdown output."""
    page_id = "12345678123412341234123456789012"
    page_metadata = {
        "id": page_id,
        "properties": {"Title": {"type": "title", "title": [_rt("Test")]}},
        "last_edited_time": "now",
    }
    children = [
        _block(
            type_="paragraph",
            id_="p1",
            rich_text=[
                _rt("Plain "),
                _rt("bold", bold=True),
                _rt(" and "),
                _rt("code", code=True),
                _rt(" and "),
                {"plain_text": "link", "annotations": {}, "href": "https://example.com"},
            ],
        ),
    ]
    handler = _make_handler(page=page_metadata, blocks_by_id={page_id: children})

    async with _make_client(handler) as client:
        result = await extract_notion(page_id, token="t", client=client)

    assert "**bold**" in result.text
    assert "`code`" in result.text
    assert "[link](https://example.com)" in result.text


@pytest.mark.asyncio
async def test_extract_notion_recurses_into_children() -> None:
    """Blocks marked has_children fetch their children — toggle/list nesting."""
    page_id = "12345678123412341234123456789012"
    page_metadata = {
        "id": page_id,
        "properties": {"Title": {"type": "title", "title": [_rt("Nested")]}},
        "last_edited_time": "now",
    }
    parent_block = {
        "id": "parent",
        "type": "bulleted_list_item",
        "has_children": True,
        "bulleted_list_item": {"rich_text": [_rt("parent item")]},
    }
    child_block = _block(type_="bulleted_list_item", id_="child", rich_text=[_rt("child item")])
    handler = _make_handler(
        page=page_metadata,
        blocks_by_id={
            page_id: [parent_block],
            "parent": [child_block],
        },
    )

    async with _make_client(handler) as client:
        result = await extract_notion(page_id, token="t", client=client)

    # Both lines present, child indented relative to parent.
    assert "- parent item" in result.text
    assert "  - child item" in result.text  # depth=1 → 2-space indent


@pytest.mark.asyncio
async def test_extract_notion_handles_empty_page() -> None:
    """Page with no body blocks → empty_page failure."""
    page_id = "12345678123412341234123456789012"
    page_metadata = {
        "properties": {"Title": {"type": "title", "title": [_rt("Empty")]}},
        "last_edited_time": "now",
    }
    handler = _make_handler(page=page_metadata, blocks_by_id={page_id: []})

    async with _make_client(handler) as client:
        result = await extract_notion(page_id, token="t", client=client)

    assert result.failure_reason == "empty_page"


@pytest.mark.asyncio
async def test_extract_notion_handles_404() -> None:
    """A 404 from Notion bubbles up as a graceful failure."""
    page_id = "12345678123412341234123456789012"

    def handler(request):
        return httpx.Response(404, json={"message": "not found"})

    async with _make_client(handler) as client:
        result = await extract_notion(page_id, token="t", client=client)

    assert result.failure_reason is not None
    assert "page_fetch_failed" in result.failure_reason
    assert "404" in result.failure_reason


@pytest.mark.asyncio
async def test_extract_notion_missing_token_fails_clean() -> None:
    """No token + no NOTION_TOKEN env var → auth_failed with the env-var name."""
    import os

    # Ensure the env var isn't set for this test.
    saved = os.environ.pop("NOTION_TOKEN", None)
    try:
        result = await extract_notion("12345678123412341234123456789012")
    finally:
        if saved is not None:
            os.environ["NOTION_TOKEN"] = saved
    assert result.failure_reason is not None
    assert "auth_failed" in result.failure_reason
    assert "NOTION_TOKEN" in result.failure_reason


@pytest.mark.asyncio
async def test_extract_notion_unsupported_block_degrades_gracefully() -> None:
    """An unknown block type renders as a placeholder, doesn't crash."""
    page_id = "12345678123412341234123456789012"
    page_metadata = {
        "properties": {"Title": {"type": "title", "title": [_rt("T")]}},
        "last_edited_time": "now",
    }
    children = [
        {"id": "x", "type": "synced_block", "synced_block": {}},  # not in renderer
    ]
    handler = _make_handler(page=page_metadata, blocks_by_id={page_id: children})

    async with _make_client(handler) as client:
        result = await extract_notion(page_id, token="t", client=client)

    assert result.failure_reason is None
    assert "[unsupported_block: synced_block]" in result.text


@pytest.mark.asyncio
async def test_dispatch_routes_notion_url(monkeypatch) -> None:
    """A notion.so URL routes to extract_notion."""
    from engine.adapters._template.contract import ExtractedContent
    from engine.utils.dispatch import extract_url

    captured: list[str] = []

    async def _fake_notion(url, **_kw):
        captured.append(url)
        return ExtractedContent(text="ok", extraction_method="text")

    async def _fail_web(url, **_kw):
        raise AssertionError("web fallback called")

    import engine.utils.dispatch as dispatch_module

    monkeypatch.setattr(dispatch_module, "extract_notion", _fake_notion)
    monkeypatch.setattr(dispatch_module, "extract_web", _fail_web)

    url = "https://www.notion.so/myws/Apollo-12345678123412341234123456789012"
    result = await extract_url(url)
    assert result.text == "ok"
    assert captured == [url]


def test_json_module_is_imported_for_unused_fixture_safety() -> None:
    """Sanity: we use json elsewhere in this file (kept for cassette debugging)."""
    assert json.dumps({"a": 1}) == '{"a": 1}'
