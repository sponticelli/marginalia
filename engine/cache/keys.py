"""Pure key builders for the L1 and L2 caches.

These functions produce hex-digest keys that are safe to use as
filenames. They include ``CACHE_VERSION`` so a single global bump
invalidates every entry on disk; per-prompt edits should bump the
prompt's frontmatter ``version`` instead, which the L1 key also
includes.

Kept separate from the stores so the contract has exactly one
definition — debug a cache miss by calling these functions in
isolation and comparing digests.
"""

from __future__ import annotations

import hashlib


def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def build_l1_key(
    *,
    content_sha: str,
    cache_version: str,
    prompt_name: str,
    prompt_version: str,
) -> str:
    """L1 (analyze-step) cache key.

    Composition: ``sha256(content_sha | CACHE_VERSION | prompt.name@prompt.version)``.

    The pipe separators prevent collisions between distinct
    ``(name, version)`` pairs that would otherwise concatenate to the
    same preimage. ``content_sha`` should already be the hex digest of
    the source body — see ``compute_content_sha256`` in
    ``engine.agents.ingest.analyze``.
    """
    preimage = f"{content_sha}|{cache_version}|{prompt_name}@{prompt_version}"
    return _sha256_hex(preimage)


def build_l2_key(
    *,
    rendered_prompt: str,
    model_id: str,
    cache_version: str,
) -> str:
    """L2 (deterministic LLM response) cache key.

    Composition: ``sha256(sha256(rendered_prompt) | model_id | CACHE_VERSION)``.

    The inner hash keeps the preimage bounded regardless of prompt
    size. ``rendered_prompt`` should be the *fully concatenated*
    system + user content the model would see — body changes
    invalidate naturally without needing a separate version field.
    """
    rendered_sha = _sha256_hex(rendered_prompt)
    preimage = f"{rendered_sha}|{model_id}|{cache_version}"
    return _sha256_hex(preimage)


__all__ = ["build_l1_key", "build_l2_key"]
