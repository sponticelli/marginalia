"""Notion source adapter — page by ID → markdown via the Notion REST API."""

from engine.adapters.notion.extractor import (
    DEFAULT_API_BASE,
    NOTION_API_VERSION,
    NotionExtractError,
    extract_notion,
    extract_notion_id,
)

__all__ = [
    "DEFAULT_API_BASE",
    "NOTION_API_VERSION",
    "NotionExtractError",
    "extract_notion",
    "extract_notion_id",
]
