"""Three-layer cache for the Marginalia engine (design §7.6).

This package owns the L1 (analyze-step) and L2 (deterministic LLM
response) caches plus the global ``CACHE_VERSION`` discipline knob.
L3 (Anthropic's native prompt cache) is wired directly into
``engine.agents.ingest.analyze.build_analyze_system_blocks``; it has
no on-disk surface here.

``CACHE_VERSION`` is the single global escape hatch for cache busts
that cross prompt boundaries — Pydantic schema edits, model swaps,
serialization changes. Edits inside one prompt should bump the
prompt's frontmatter ``version`` instead; the L1 key includes both.
Bump ``CACHE_VERSION`` whenever a change makes *every* existing
cache entry stale.

Storage: ``<wiki_root>/.wiki/cache/{analysis,llm}/<key>.json``.
"""

from __future__ import annotations

CACHE_VERSION = "v1"

from engine.cache.keys import build_l1_key, build_l2_key  # noqa: E402

__all__ = [
    "CACHE_VERSION",
    "build_l1_key",
    "build_l2_key",
]
