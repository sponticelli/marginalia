"""Adapter contract reference (design §8 source adapters).

An adapter is a function that resolves a source — a file on disk, a
Notion URL, a YouTube video, etc. — into ``ExtractedContent``: a
deterministic-shaped Pydantic record carrying the text the ingest
agent will reason over, plus enough metadata for cost tracking and
the cache key.

New adapters must:

1. Accept the source identifier as their first positional argument
   (e.g. ``Path`` for local files, ``str`` URL for remote sources).
2. Accept an optional injected ``client`` for testability when they
   make Anthropic calls (vision/document fallback, transcription).
3. Return an ``ExtractedContent`` instance — never raw strings, never
   tuples.
4. Set ``extraction_method`` to one of the documented literals so
   downstream consumers can branch on provenance.
5. Populate ``cost_usd`` when a vision/LLM call was made; leave it
   ``None`` when only text-extraction tools (pypdf, etc.) ran.

The contract is intentionally narrow: extraction belongs *before*
``analyze_source``, so adapters return text and let the ingest
pipeline (`engine.agents.ingest`) take it from there. Multi-modal
provenance lives in this record, not in the ``SourceKind`` enum
(which still denotes upstream source, not file type).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ExtractionMethod = Literal[
    "text",  # extracted from a text layer (pypdf, plain markdown, etc.)
    "vision_document",  # native Anthropic document content block
    "vision_rasterized",  # per-page rasterization + image content blocks
    "vision_image",  # single image (.png/.jpg) via image content block
    "youtube_transcript",  # YouTube transcript via youtube-transcript-api
]


class ExtractedContent(BaseModel):
    """Adapter output: extracted text + extraction provenance.

    The shape downstream consumers (analyze step, cost tracker, cache
    key derivation) can rely on. The ``text`` field is always
    populated; everything else is best-effort metadata.
    """

    model_config = ConfigDict(extra="forbid")

    text: str = Field(
        description="Extracted text content; empty string is allowed for vision misses or graceful failure.",
    )
    pages: list[str] | None = Field(
        default=None,
        description=(
            "Per-page or per-segment split. PDFs use this for per-page text; "
            "YouTube uses it for per-snippet '[MM:SS] line' strings. None for "
            "non-paginated, non-segmented content."
        ),
    )
    extraction_method: ExtractionMethod = Field(
        description="How the text was obtained; lets downstream branch on provenance.",
    )
    chars_per_page: float | None = Field(
        default=None,
        description="Mean chars per page from the text layer; populated for PDFs.",
    )
    cost_usd: float | None = Field(
        default=None,
        ge=0.0,
        description="USD spent on vision/LLM calls; None for pure text-extraction paths.",
    )
    failure_reason: str | None = Field(
        default=None,
        description=(
            "Set when extraction failed gracefully and ``text`` is empty. "
            "Adapters return a structured failed-with-reason result instead "
            "of raising, so dispatchers can collect partial success across a batch."
        ),
    )


__all__ = ["ExtractedContent", "ExtractionMethod"]
