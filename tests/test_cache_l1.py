"""L1 analysis cache: hit/miss, atomic writes, version-bump invalidation.

The bug-class this layer prevents is "stale cache served after prompt
edit." The version-sensitivity tests below are the regression check
that the demo-doc claim still holds.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from engine.agents.ingest.analyze import (
    SourceAnalysis,
    compute_content_sha256,
)
from engine.cache import CACHE_VERSION
from engine.cache.decorators import cached_analyze
from engine.cache.keys import build_l1_key
from engine.cache.l1_analysis import AnalysisCache
from engine.models.pages import PageType, SourceKind
from engine.prompts import Prompt


def _stub_prompt(*, name: str = "ingest_analyze", version: str = "v1") -> Prompt:
    return Prompt(
        name=name,
        version=version,
        role="ingest.analyze",
        model="claude-haiku-4-5",
        system="You are the analyzer.",
        user_template="<source>{content}</source>{sha}{source_kind}{schema_json}",
    )


def _good_payload(sha: str) -> dict:
    return {
        "proposed_title": "Apollo Launch Notes",
        "proposed_type": "meeting",
        "summary": "A faithful summary that satisfies the twenty-character minimum.",
        "entities": ["Sandro", "Apollo"],
        "proposed_tags": ["apollo"],
        "source_kind": "local_file",
        "content_sha256": sha,
    }


def test_set_and_get_round_trip(tmp_path: Path) -> None:
    cache = AnalysisCache(tmp_path / "analysis")
    sha = compute_content_sha256("hello")
    analysis = SourceAnalysis.model_validate(_good_payload(sha))
    key = build_l1_key(
        content_sha=sha,
        cache_version=CACHE_VERSION,
        prompt_name="ingest_analyze",
        prompt_version="v1",
    )
    cache.set(key, analysis)
    out = cache.get(key)
    assert out is not None
    assert out.proposed_type == PageType.MEETING
    assert out.content_sha256 == sha


def test_get_returns_none_on_miss(tmp_path: Path) -> None:
    cache = AnalysisCache(tmp_path / "analysis")
    assert cache.get("does-not-exist") is None


def test_corrupt_entry_is_treated_as_miss(tmp_path: Path) -> None:
    cache = AnalysisCache(tmp_path / "analysis")
    path = cache.path_for("abc")
    path.write_text("not json", encoding="utf-8")
    assert cache.get("abc") is None


def test_clear_removes_all_entries(tmp_path: Path) -> None:
    cache = AnalysisCache(tmp_path / "analysis")
    sha = compute_content_sha256("hello")
    analysis = SourceAnalysis.model_validate(_good_payload(sha))
    cache.set("k1", analysis)
    cache.set("k2", analysis)
    n = cache.clear()
    assert n == 2
    assert cache.get("k1") is None


def test_envelope_carries_cache_version(tmp_path: Path) -> None:
    cache = AnalysisCache(tmp_path / "analysis")
    sha = compute_content_sha256("hello")
    analysis = SourceAnalysis.model_validate(_good_payload(sha))
    cache.set("k", analysis)
    payload = json.loads(cache.path_for("k").read_text(encoding="utf-8"))
    assert payload["cache_version"] == CACHE_VERSION
    assert "cached_at" in payload


def test_concurrent_writes_produce_one_valid_file(tmp_path: Path) -> None:
    """Two threads racing on the same key must not corrupt the file."""
    cache = AnalysisCache(tmp_path / "analysis")
    sha = compute_content_sha256("hello")
    analysis = SourceAnalysis.model_validate(_good_payload(sha))

    def writer(_: int) -> None:
        cache.set("racing-key", analysis)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(writer, range(20)))

    out = cache.get("racing-key")
    assert out is not None
    assert out.content_sha256 == sha


@pytest.mark.asyncio
async def test_cached_analyze_miss_then_hit(tmp_path, stub_client, minimal_config) -> None:
    cache = AnalysisCache(tmp_path / "analysis")
    content = "Apollo launch sync notes; Sandro and Priya reviewed blockers."
    sha = compute_content_sha256(content)
    stub_client.texts = [json.dumps(_good_payload(sha))]
    prompt = _stub_prompt()

    a, hit_a = await cached_analyze(
        content,
        SourceKind.LOCAL_FILE,
        minimal_config,
        cache=cache,
        client=stub_client,
        prompt=prompt,
    )
    assert hit_a is False
    assert len(stub_client.calls) == 1

    b, hit_b = await cached_analyze(
        content,
        SourceKind.LOCAL_FILE,
        minimal_config,
        cache=cache,
        client=stub_client,
        prompt=prompt,
    )
    assert hit_b is True
    assert len(stub_client.calls) == 1  # no new API call
    assert b.proposed_title == a.proposed_title


@pytest.mark.asyncio
async def test_cached_analyze_invalidates_on_prompt_version_bump(
    tmp_path, stub_client, minimal_config
) -> None:
    """Bumping prompt.version is the per-prompt invalidation lever."""
    cache = AnalysisCache(tmp_path / "analysis")
    content = "Apollo launch sync notes; Sandro and Priya reviewed blockers."
    sha = compute_content_sha256(content)
    stub_client.texts = [json.dumps(_good_payload(sha))] * 2

    _, hit_v1 = await cached_analyze(
        content,
        SourceKind.LOCAL_FILE,
        minimal_config,
        cache=cache,
        client=stub_client,
        prompt=_stub_prompt(version="v1"),
    )
    _, hit_v1_again = await cached_analyze(
        content,
        SourceKind.LOCAL_FILE,
        minimal_config,
        cache=cache,
        client=stub_client,
        prompt=_stub_prompt(version="v1"),
    )
    _, hit_v2 = await cached_analyze(
        content,
        SourceKind.LOCAL_FILE,
        minimal_config,
        cache=cache,
        client=stub_client,
        prompt=_stub_prompt(version="v2"),
    )

    assert hit_v1 is False  # first call, miss
    assert hit_v1_again is True  # same version, hit
    assert hit_v2 is False  # bumped version → miss
    assert len(stub_client.calls) == 2  # only the two misses called the API
