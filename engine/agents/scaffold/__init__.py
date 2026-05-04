"""Scaffold agent — regenerate wiki meta-pages from page state (design §7.1)."""

from engine.agents.scaffold.main import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    INDEX_FILENAME,
    INDEX_PROMPT_NAME,
    SUPPORTED_TARGETS,
    ScaffoldResult,
    ScaffoldTarget,
    format_pages_listing,
    scaffold_index,
)

__all__ = [
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_MODEL",
    "INDEX_FILENAME",
    "INDEX_PROMPT_NAME",
    "SUPPORTED_TARGETS",
    "ScaffoldResult",
    "ScaffoldTarget",
    "format_pages_listing",
    "scaffold_index",
]
