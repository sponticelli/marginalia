"""Cross-source synthesis (design §7.1)."""

from engine.agents.synthesis.cross_source import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    MAX_ATTEMPTS,
    PROMPT_NAME,
    derive_default_path,
    extract_wikilinks,
    synthesize_cross_source,
    verify_citations,
)

__all__ = [
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_MODEL",
    "MAX_ATTEMPTS",
    "PROMPT_NAME",
    "derive_default_path",
    "extract_wikilinks",
    "synthesize_cross_source",
    "verify_citations",
]
