"""L2 deterministic-response cache: hit/miss + model-id sensitivity."""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.cache.decorators import cached_llm_call
from engine.cache.l2_responses import CachedLLMResponse, ResponseCache


def test_set_and_get_round_trip(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path / "llm")
    payload = CachedLLMResponse(
        text="hello world",
        model="claude-haiku-4-5",
        tokens_in=42,
        tokens_out=7,
    )
    cache.set("k1", payload)
    out = cache.get("k1")
    assert out is not None
    assert out.text == "hello world"
    assert out.tokens_in == 42


def test_get_returns_none_on_miss(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path / "llm")
    assert cache.get("none") is None


@pytest.mark.asyncio
async def test_cached_llm_call_miss_then_hit(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path / "llm")
    calls = {"n": 0}

    async def fake_call() -> CachedLLMResponse:
        calls["n"] += 1
        return CachedLLMResponse(
            text="answer",
            model="claude-haiku-4-5",
            tokens_in=100,
            tokens_out=10,
        )

    a, hit_a = await cached_llm_call(
        cache=cache,
        rendered_prompt="prompt body",
        model="claude-haiku-4-5",
        fn=fake_call,
    )
    b, hit_b = await cached_llm_call(
        cache=cache,
        rendered_prompt="prompt body",
        model="claude-haiku-4-5",
        fn=fake_call,
    )
    assert hit_a is False
    assert hit_b is True
    assert calls["n"] == 1
    assert b.text == a.text


@pytest.mark.asyncio
async def test_cached_llm_call_distinguishes_models(tmp_path: Path) -> None:
    """Same prompt, different model → cache miss (correct: outputs differ per model)."""
    cache = ResponseCache(tmp_path / "llm")
    calls = {"n": 0}

    async def fake_call() -> CachedLLMResponse:
        calls["n"] += 1
        return CachedLLMResponse(
            text=f"reply {calls['n']}",
            model="x",
            tokens_in=1,
            tokens_out=1,
        )

    await cached_llm_call(
        cache=cache, rendered_prompt="same prompt", model="claude-haiku-4-5", fn=fake_call
    )
    _, hit = await cached_llm_call(
        cache=cache, rendered_prompt="same prompt", model="claude-sonnet-4-6", fn=fake_call
    )
    assert hit is False
    assert calls["n"] == 2
