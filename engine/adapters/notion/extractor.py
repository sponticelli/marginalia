"""Notion adapter — page by ID → markdown via the REST API.

Why direct REST instead of ``notion-client``: the API surface we
actually need is small (two endpoints — page metadata + block
children), and ``notion-client`` adds a sync-only client we'd then
have to wrap in ``asyncio.to_thread``. ``httpx`` (already a dep for
the gmail/web adapters) gives us native async with ~30 lines of glue.

Block-tree handling: Notion represents a page body as a flat list of
blocks; "child" blocks (toggle contents, list nesting) require
recursing per-block via ``GET /blocks/{id}/children``. We do this
lazily — only blocks that explicitly carry ``has_children: true`` get
a follow-up fetch. The renderer covers the most common types
(paragraph / heading_1-3 / bulleted_list_item / numbered_list_item /
to_do / code / quote / divider / child_page); less common types
degrade gracefully to ``[unsupported_block: <type>]`` so we never
silently drop content.
"""

from __future__ import annotations

import os
import re
from typing import Any

from engine.adapters._template.contract import ExtractedContent

DEFAULT_API_BASE = "https://api.notion.com"
NOTION_API_VERSION = "2022-06-28"  # stable version used by Notion's docs
DEFAULT_TIMEOUT_S = 30.0
TOKEN_ENV = "NOTION_TOKEN"

# Notion IDs are UUIDs, often dash-stripped (32 hex chars) or
# dash-formatted (36 with dashes).
_NOTION_ID_RE = re.compile(
    r"^[0-9a-fA-F]{32}$|^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
# URL form: notion.so/<workspace>/<title-and-id> where the trailing
# 32 chars (after the last dash) are the page ID.
_URL_ID_RE = re.compile(r"([0-9a-fA-F]{32})(?:[?#]|$)")


class NotionExtractError(ValueError):
    """Raised when input isn't a recognizable Notion URL or ID."""


def extract_notion_id(url_or_id: str) -> str:
    """Normalize URL or bare-ID input to a dash-stripped 32-char ID."""
    candidate = url_or_id.strip()
    if _NOTION_ID_RE.match(candidate):
        return candidate.replace("-", "")
    match = _URL_ID_RE.search(candidate)
    if match:
        return match.group(1)
    raise NotionExtractError(
        f"could not extract Notion page ID from {url_or_id!r}; "
        "expected a notion.so URL or a 32-char hex ID"
    )


# ─── rendering ──────────────────────────────────────────────────────


def _render_rich_text(rich_text: list[dict]) -> str:
    """Render Notion's rich-text array (one block worth) to markdown."""
    out: list[str] = []
    for span in rich_text:
        text = span.get("plain_text", "")
        annotations = span.get("annotations", {}) or {}
        if annotations.get("code"):
            text = f"`{text}`"
        if annotations.get("bold"):
            text = f"**{text}**"
        if annotations.get("italic"):
            text = f"*{text}*"
        if annotations.get("strikethrough"):
            text = f"~~{text}~~"
        href = span.get("href")
        if href:
            text = f"[{text}]({href})"
        out.append(text)
    return "".join(out)


def _render_block(block: dict, *, depth: int = 0) -> str:
    """Render one block to its markdown line(s). Children handled by caller."""
    bt = block.get("type", "")
    payload = block.get(bt, {}) or {}
    indent = "  " * depth
    if bt == "paragraph":
        return f"{indent}{_render_rich_text(payload.get('rich_text', []))}"
    if bt == "heading_1":
        return f"{indent}# {_render_rich_text(payload.get('rich_text', []))}"
    if bt == "heading_2":
        return f"{indent}## {_render_rich_text(payload.get('rich_text', []))}"
    if bt == "heading_3":
        return f"{indent}### {_render_rich_text(payload.get('rich_text', []))}"
    if bt == "bulleted_list_item":
        return f"{indent}- {_render_rich_text(payload.get('rich_text', []))}"
    if bt == "numbered_list_item":
        # Caller numbering is hard with Notion's flat list shape; a leading
        # `1.` works in markdown — renderers re-number sequentially.
        return f"{indent}1. {_render_rich_text(payload.get('rich_text', []))}"
    if bt == "to_do":
        checked = payload.get("checked", False)
        box = "[x]" if checked else "[ ]"
        return f"{indent}- {box} {_render_rich_text(payload.get('rich_text', []))}"
    if bt == "code":
        lang = payload.get("language", "") or ""
        body = _render_rich_text(payload.get("rich_text", []))
        return f"{indent}```{lang}\n{body}\n{indent}```"
    if bt == "quote":
        return f"{indent}> {_render_rich_text(payload.get('rich_text', []))}"
    if bt == "divider":
        return f"{indent}---"
    if bt == "child_page":
        title = payload.get("title", "(untitled child page)")
        return f"{indent}- [[{title}]] *(linked Notion subpage)*"
    return f"{indent}[unsupported_block: {bt}]"


def _render_blocks(blocks: list[dict], *, depth: int = 0) -> list[str]:
    """Walk a block list, recursing into ``children`` when present."""
    lines: list[str] = []
    for block in blocks:
        lines.append(_render_block(block, depth=depth))
        children = block.get("children", []) or []
        if children:
            lines.extend(_render_blocks(children, depth=depth + 1))
    return lines


def _page_title(page_metadata: dict) -> str:
    """Pull the title out of a page's properties dict.

    Notion stores page titles in a property whose ``type`` is
    ``"title"``; the property name varies (often "Name" or "Title").
    We scan all properties for the one with type=title.
    """
    properties = page_metadata.get("properties") or {}
    for prop in properties.values():
        if not isinstance(prop, dict):
            continue
        if prop.get("type") == "title":
            return _render_rich_text(prop.get("title", []))
    return "(untitled)"


def _failure(reason: str) -> ExtractedContent:
    return ExtractedContent(
        text="",
        pages=None,
        extraction_method="text",
        failure_reason=reason,
    )


# ─── HTTP plumbing ──────────────────────────────────────────────────


async def _fetch_page(client, page_id: str) -> dict:
    resp = await client.get(f"/v1/pages/{page_id}")
    resp.raise_for_status()
    return resp.json()


async def _fetch_block_children(client, block_id: str) -> list[dict]:
    """Page through ``GET /v1/blocks/{id}/children`` until cursor exhausts."""
    out: list[dict] = []
    cursor: str | None = None
    while True:
        params: dict[str, Any] = {"page_size": 100}
        if cursor:
            params["start_cursor"] = cursor
        resp = await client.get(f"/v1/blocks/{block_id}/children", params=params)
        resp.raise_for_status()
        data = resp.json()
        out.extend(data.get("results", []) or [])
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return out


async def _fetch_full_tree(client, root_id: str) -> list[dict]:
    """Fetch every block under ``root_id``, attaching ``children`` in-place."""
    blocks = await _fetch_block_children(client, root_id)
    for block in blocks:
        if block.get("has_children"):
            block["children"] = await _fetch_full_tree(client, block["id"])
    return blocks


# ─── public entry ───────────────────────────────────────────────────


async def extract_notion(
    url_or_id: str,
    *,
    token: str | None = None,
    api_base: str = DEFAULT_API_BASE,
    client=None,
) -> ExtractedContent:
    """Fetch a Notion page (with full block tree) → markdown.

    Auth resolution:

    1. Explicit ``token`` argument (test path, or callers reading from
       a different env var).
    2. ``NOTION_TOKEN`` env var (production).

    The ``client`` argument lets tests inject a configured ``httpx``
    client — particularly useful for ``httpx.MockTransport`` cassettes.
    """
    try:
        page_id = extract_notion_id(url_or_id)
    except NotionExtractError as exc:
        return _failure(f"invalid_page_id: {exc}")

    auth_token = token or os.environ.get(TOKEN_ENV)
    if not auth_token:
        return _failure(f"auth_failed: missing {TOKEN_ENV} env var (Notion integration token)")

    import httpx

    own_client = client is None
    if client is None:
        client = httpx.AsyncClient(
            base_url=api_base,
            headers={
                "Authorization": f"Bearer {auth_token}",
                "Notion-Version": NOTION_API_VERSION,
                "Content-Type": "application/json",
            },
            timeout=DEFAULT_TIMEOUT_S,
        )

    try:
        try:
            page_metadata = await _fetch_page(client, page_id)
        except httpx.HTTPStatusError as exc:
            return _failure(f"page_fetch_failed: HTTP {exc.response.status_code}")
        except Exception as exc:  # noqa: BLE001
            return _failure(f"page_fetch_failed: {type(exc).__name__}: {exc}")

        try:
            blocks = await _fetch_full_tree(client, page_id)
        except httpx.HTTPStatusError as exc:
            return _failure(f"blocks_fetch_failed: HTTP {exc.response.status_code}")
        except Exception as exc:  # noqa: BLE001
            return _failure(f"blocks_fetch_failed: {type(exc).__name__}: {exc}")
    finally:
        if own_client:
            await client.aclose()

    if not blocks:
        return _failure("empty_page")

    title = _page_title(page_metadata)
    last_edited = page_metadata.get("last_edited_time", "unknown")
    rendered = "\n\n".join(line for line in _render_blocks(blocks) if line.strip())
    body = f"# {title}\n\nSource: Notion page {page_id}\nLast edited: {last_edited}\n\n{rendered}\n"

    return ExtractedContent(
        text=body,
        pages=None,
        extraction_method="text",
        cost_usd=None,
        failure_reason=None,
    )


__all__ = [
    "DEFAULT_API_BASE",
    "NOTION_API_VERSION",
    "TOKEN_ENV",
    "NotionExtractError",
    "extract_notion",
    "extract_notion_id",
]
