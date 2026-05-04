"""Cache key contract: deterministic, version-sensitive, collision-free.

These keys are filenames on disk. Changing how they're built is a
breaking change for any cache that already exists — the test suite is
the contract.
"""

from __future__ import annotations

from engine.cache.keys import build_l1_key, build_l2_key


def test_l1_key_is_deterministic() -> None:
    a = build_l1_key(
        content_sha="a" * 64,
        cache_version="v1",
        prompt_name="ingest_analyze",
        prompt_version="v1",
    )
    b = build_l1_key(
        content_sha="a" * 64,
        cache_version="v1",
        prompt_name="ingest_analyze",
        prompt_version="v1",
    )
    assert a == b
    assert len(a) == 64  # sha256 hex


def test_l1_key_changes_with_cache_version() -> None:
    a = build_l1_key(
        content_sha="a" * 64,
        cache_version="v1",
        prompt_name="ingest_analyze",
        prompt_version="v1",
    )
    b = build_l1_key(
        content_sha="a" * 64,
        cache_version="v2",
        prompt_name="ingest_analyze",
        prompt_version="v1",
    )
    assert a != b


def test_l1_key_changes_with_prompt_version() -> None:
    a = build_l1_key(
        content_sha="a" * 64,
        cache_version="v1",
        prompt_name="ingest_analyze",
        prompt_version="v1",
    )
    b = build_l1_key(
        content_sha="a" * 64,
        cache_version="v1",
        prompt_name="ingest_analyze",
        prompt_version="v2",
    )
    assert a != b


def test_l1_key_changes_with_content() -> None:
    a = build_l1_key(
        content_sha="a" * 64,
        cache_version="v1",
        prompt_name="ingest_analyze",
        prompt_version="v1",
    )
    b = build_l1_key(
        content_sha="b" * 64,
        cache_version="v1",
        prompt_name="ingest_analyze",
        prompt_version="v1",
    )
    assert a != b


def test_l1_key_separates_name_and_version() -> None:
    """Pipe-separated preimage prevents (name, version) concatenation collisions."""
    a = build_l1_key(
        content_sha="a" * 64,
        cache_version="v1",
        prompt_name="ingest",
        prompt_version="analyze_v1",
    )
    b = build_l1_key(
        content_sha="a" * 64,
        cache_version="v1",
        prompt_name="ingest_analyze",
        prompt_version="v1",
    )
    assert a != b


def test_l2_key_is_deterministic() -> None:
    a = build_l2_key(rendered_prompt="hello", model_id="claude-haiku-4-5", cache_version="v1")
    b = build_l2_key(rendered_prompt="hello", model_id="claude-haiku-4-5", cache_version="v1")
    assert a == b
    assert len(a) == 64


def test_l2_key_changes_with_model() -> None:
    a = build_l2_key(rendered_prompt="hello", model_id="claude-haiku-4-5", cache_version="v1")
    b = build_l2_key(rendered_prompt="hello", model_id="claude-sonnet-4-6", cache_version="v1")
    assert a != b


def test_l2_key_changes_with_prompt() -> None:
    a = build_l2_key(rendered_prompt="hello", model_id="claude-haiku-4-5", cache_version="v1")
    b = build_l2_key(rendered_prompt="hello!", model_id="claude-haiku-4-5", cache_version="v1")
    assert a != b


def test_l2_key_changes_with_cache_version() -> None:
    a = build_l2_key(rendered_prompt="hello", model_id="claude-haiku-4-5", cache_version="v1")
    b = build_l2_key(rendered_prompt="hello", model_id="claude-haiku-4-5", cache_version="v2")
    assert a != b
