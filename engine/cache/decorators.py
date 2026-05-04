"""High-level wrappers that combine key construction + store I/O.

These are not Python ``@decorator`` syntax — call them as functions
that return ``(result, was_hit)``. Returning the hit flag lets
callers log a ``CostRecord(cached=True)`` to the audit trail without
us having to thread a logger through the cache layer. Notebooks and
the worker treat the flag as an observability hook.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from engine.agents.ingest.analyze import (
    DEFAULT_MAX_TOKENS,
    PROMPT_NAME,
    SourceAnalysis,
    analyze_source,
    compute_content_sha256,
)
from engine.cache import CACHE_VERSION
from engine.cache.keys import build_l1_key, build_l2_key
from engine.cache.l1_analysis import AnalysisCache
from engine.cache.l2_responses import CachedLLMResponse, ResponseCache
from engine.prompts import Prompt, load_prompt
from engine.utils.cost_tracker import CostRecord, record_attempt

if TYPE_CHECKING:
    from anthropic import Anthropic

    from engine.models.pages import SourceKind
    from engine.models.wiki_config import MarginaliaConfig


async def cached_analyze(
    content: str,
    source_kind: SourceKind,
    config: MarginaliaConfig,
    *,
    cache: AnalysisCache,
    client: Anthropic | None = None,
    prompt: Prompt | None = None,
    model: str | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    on_cost: Callable[[CostRecord], None] | None = None,
) -> tuple[SourceAnalysis, bool]:
    """L1-cached wrapper around ``analyze_source``.

    Returns ``(analysis, was_hit)``. On a hit, the cached
    ``SourceAnalysis`` is returned without touching Anthropic and a
    ``CostRecord(cached=True, tokens_in=0, tokens_out=0)`` is emitted
    via ``on_cost`` so audit-side aggregations can count the hit. On
    a miss, ``on_cost`` forwards to ``analyze_source`` so the API
    call's tokens land as a normal ``cached=False`` record.

    The L1 key folds in ``content_sha + CACHE_VERSION + prompt.name +
    prompt.version`` — editing a prompt body without bumping its
    frontmatter ``version`` produces a stale hit. ``CACHE_VERSION`` is
    the global escape hatch for cross-prompt invalidations.
    """
    prompt = prompt or load_prompt(PROMPT_NAME)
    content_sha = compute_content_sha256(content)
    key = build_l1_key(
        content_sha=content_sha,
        cache_version=CACHE_VERSION,
        prompt_name=prompt.name,
        prompt_version=prompt.version,
    )

    hit = cache.get(key)
    if hit is not None:
        if on_cost is not None:
            on_cost(
                record_attempt(
                    agent="ingest",
                    model=model or prompt.model or "claude-haiku-4-5",
                    tokens_in=0,
                    tokens_out=0,
                    cached=True,
                )
            )
        return hit, True

    analysis = await analyze_source(
        content,
        source_kind,
        config,
        client=client,
        prompt=prompt,
        model=model,
        max_tokens=max_tokens,
        on_cost=on_cost,
    )
    cache.set(key, analysis)
    return analysis, False


async def cached_llm_call(
    *,
    cache: ResponseCache,
    rendered_prompt: str,
    model: str,
    fn: Callable[[], Awaitable[CachedLLMResponse]],
    agent: str = "qa",
    on_cost: Callable[[CostRecord], None] | None = None,
) -> tuple[CachedLLMResponse, bool]:
    """L2-cached wrapper for any deterministic LLM call.

    Caller renders the full prompt (system + user) into one string,
    names the model, and supplies an async callable that performs the
    actual API call when needed. A cache hit short-circuits ``fn``.

    On hit, emits a ``CostRecord(cached=True, tokens_in=0)``. On miss,
    builds a normal ``CostRecord`` from the response's reported tokens
    and the supplied ``agent`` label.
    """
    key = build_l2_key(
        rendered_prompt=rendered_prompt,
        model_id=model,
        cache_version=CACHE_VERSION,
    )
    hit = cache.get(key)
    if hit is not None:
        if on_cost is not None:
            on_cost(
                record_attempt(
                    agent=agent,
                    model=model,
                    tokens_in=0,
                    tokens_out=0,
                    cached=True,
                )
            )
        return hit, True

    result = await fn()
    cache.set(key, result)
    if on_cost is not None:
        on_cost(
            record_attempt(
                agent=agent,
                model=model,
                tokens_in=result.tokens_in,
                tokens_out=result.tokens_out,
                cached=False,
            )
        )
    return result, False


__all__ = ["cached_analyze", "cached_llm_call"]
