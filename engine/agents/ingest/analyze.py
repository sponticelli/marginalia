"""Cacheable ingest analyze step (design §7.3, §7.6 L1).

`analyze_source` is the ingest step-1 contract: a deterministic,
prompt-versioned, pure function over source content. Its output
(`SourceAnalysis`) intentionally carries bare entity names, not
wikilinks — wikilink resolution is state-dependent and belongs to
synthesize. Keeping these split is what makes the analyze layer
cacheable on `sha256(content) + CACHE_VERSION + prompt.version`.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from engine.models.pages import Confidence, PageType, SourceKind
from engine.prompts import Prompt, load_prompt
from engine.utils.api_compat import temperature_kwargs
from engine.utils.cost_tracker import CostRecord, record_attempt

if TYPE_CHECKING:
    from anthropic import Anthropic

    from engine.models.wiki_config import MarginaliaConfig

PROMPT_NAME = "ingest_analyze"
DEFAULT_MODEL = "claude-haiku-4-5"
DEFAULT_MAX_TOKENS = 1024


class SourceAnalysis(BaseModel):
    """Structured output from the cacheable analyze step."""

    model_config = ConfigDict(extra="forbid")

    proposed_title: str = Field(
        min_length=1,
        description="Candidate title inferred from the source content.",
    )
    proposed_type: PageType = Field(
        description="Candidate wiki page type inferred from the source.",
    )
    summary: str = Field(
        min_length=20,
        max_length=2000,
        description="Faithful summary of the source content.",
    )
    entities: list[str] = Field(
        default_factory=list,
        description=(
            "Bare entity names extracted from content; wikilink resolution belongs to synthesize."
        ),
    )
    proposed_tags: list[str] = Field(
        default_factory=list,
        description="Candidate tags inferred from source content.",
    )
    source_kind: SourceKind = Field(
        description="Adapter/source kind that produced this analysis.",
    )
    confidence: Confidence | None = Field(
        default=None,
        description="Optional confidence for the extracted analysis.",
    )
    content_sha256: str = Field(
        pattern=r"^[a-f0-9]{64}$",
        description="SHA-256 digest of original source content; cache key spine.",
    )


def compute_content_sha256(content: str) -> str:
    """Return deterministic SHA-256 digest for analyze-step caching."""
    return hashlib.sha256(content.encode()).hexdigest()


def _parse_json_object(raw: str) -> dict:
    """Extract and parse the first JSON object from model text output."""
    text = raw.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        return json.loads(text[start : end + 1])


def build_analyze_system(prompt: Prompt, config: MarginaliaConfig) -> str:
    """Compose the analyze system prompt as a single string.

    Kept for tests and string-mode call sites. Production calls use
    ``build_analyze_system_blocks`` so Anthropic's prompt cache (L3,
    design §7.6) can attach to the wiki-config tail.
    """
    return (
        prompt.system
        + f"\n\n<wiki_purpose>\n{config.purpose_body}\n</wiki_purpose>"
        + f"\n\n<style_guide>\n{config.agents_body}\n</style_guide>"
    )


def build_analyze_system_blocks(prompt: Prompt, config: MarginaliaConfig) -> list[dict]:
    """Compose the analyze system prompt as cacheable Anthropic content blocks.

    Returns a single text block whose tail is marked
    ``cache_control: {"type": "ephemeral"}`` — Anthropic's prompt cache
    will reuse the prefix on subsequent calls with the same content,
    cutting input-token cost on every ingest after the first.

    The combined block must clear Anthropic's per-model minimum
    (~1024 tokens for Haiku/Sonnet 4.x); the wiki config bodies
    typically push it well past that. Below the minimum the block is
    silently not cached — no error.
    """
    text = build_analyze_system(prompt, config)
    return [
        {
            "type": "text",
            "text": text,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def _record_usage(usage_obj: object, out: dict) -> None:
    """Populate ``out`` with token counts from an Anthropic usage object.

    Cache-related fields default to 0 when the SDK omits them (older
    clients, non-cacheable calls) — keeps downstream code simple.
    """
    out["input_tokens"] = int(getattr(usage_obj, "input_tokens", 0) or 0)
    out["output_tokens"] = int(getattr(usage_obj, "output_tokens", 0) or 0)
    out["cache_creation_input_tokens"] = int(
        getattr(usage_obj, "cache_creation_input_tokens", 0) or 0
    )
    out["cache_read_input_tokens"] = int(getattr(usage_obj, "cache_read_input_tokens", 0) or 0)


async def analyze_source(
    content: str,
    source_kind: SourceKind,
    config: MarginaliaConfig,
    *,
    client: Anthropic | None = None,
    prompt: Prompt | None = None,
    model: str | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    prompt_cache: bool = True,
    out_usage: dict | None = None,
    on_cost: Callable[[CostRecord], None] | None = None,
) -> SourceAnalysis:
    """Run the cacheable ingest analyze step on one source body.

    Pure over ``(content, source_kind, prompt.version)`` — safe to
    L1-cache on ``sha256(content_sha | CACHE_VERSION | prompt.name@version)``.
    ``temperature=0`` is passed when the model accepts it (Haiku 4.5,
    Sonnet 4.6) and omitted for Opus 4.7, which deprecated it.

    L3 (Anthropic's native prompt cache) is on by default via
    ``prompt_cache=True``: the system prompt is sent as a content
    block list with ``cache_control: ephemeral``. Pass ``False`` to
    fall back to legacy string-mode (existing tests, hosts that don't
    speak the blocks form). When ``out_usage`` is supplied the caller
    receives input/output/cache_creation/cache_read token counts —
    needed to prove L3 is firing in receipts.

    ``client`` and ``prompt`` are injected for testability. The
    content hash is computed here (never trusted from the model) so
    it can serve as the cache key.
    """
    prompt = prompt or load_prompt(PROMPT_NAME)
    if client is None:
        from anthropic import Anthropic as _Anthropic

        client = _Anthropic()

    sha = compute_content_sha256(content)
    user_msg = prompt.user_template.format(
        source_kind=source_kind.value,
        sha=sha,
        content=content,
        schema_json=json.dumps(SourceAnalysis.model_json_schema()),
    )
    system: str | list[dict]
    if prompt_cache:
        system = build_analyze_system_blocks(prompt, config)
    else:
        system = build_analyze_system(prompt, config)

    chosen_model = model or prompt.model or DEFAULT_MODEL
    resp = client.messages.create(
        model=chosen_model,
        max_tokens=max_tokens,
        **temperature_kwargs(chosen_model),
        system=system,
        messages=[{"role": "user", "content": user_msg}],
    )
    if out_usage is not None:
        _record_usage(resp.usage, out_usage)
    if on_cost is not None:
        on_cost(
            record_attempt(
                agent="ingest",
                model=chosen_model,
                tokens_in=int(getattr(resp.usage, "input_tokens", 0) or 0),
                tokens_out=int(getattr(resp.usage, "output_tokens", 0) or 0),
                cached=False,
            )
        )
    data = _parse_json_object(resp.content[0].text)
    data["content_sha256"] = sha
    return SourceAnalysis.model_validate(data)


__all__ = [
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_MODEL",
    "PROMPT_NAME",
    "SourceAnalysis",
    "analyze_source",
    "build_analyze_system",
    "build_analyze_system_blocks",
    "compute_content_sha256",
]
