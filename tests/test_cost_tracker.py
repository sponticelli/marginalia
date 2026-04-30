"""CostRecord + record_attempt arithmetic and validation."""

import pytest

from engine.utils.cost_tracker import (
    CostRecord,
    UnknownModelError,
    estimate_cost_usd,
    record_attempt,
)


def test_estimate_haiku():
    # Haiku: $1/M in, $5/M out  →  1M in + 1M out = 6.0
    assert estimate_cost_usd("claude-haiku-4-5", 1_000_000, 1_000_000) == pytest.approx(6.0)


def test_estimate_sonnet_small():
    # Sonnet: $3/M in, $15/M out  →  1000 in + 100 out = 0.003 + 0.0015 = 0.0045
    assert estimate_cost_usd("claude-sonnet-4-6", 1000, 100) == pytest.approx(0.0045)


def test_unknown_model_raises():
    with pytest.raises(UnknownModelError):
        estimate_cost_usd("nonexistent-model", 1, 1)


def test_record_attempt_shape():
    r = record_attempt(agent="ingest", model="claude-haiku-4-5", tokens_in=2000, tokens_out=500)
    assert isinstance(r, CostRecord)
    assert r.agent == "ingest"
    assert r.model == "claude-haiku-4-5"
    assert r.tokens_in == 2000
    assert r.tokens_out == 500
    assert r.cost_usd == pytest.approx(0.002 + 0.0025)
    assert r.cached is False
    assert r.id  # uuid populated
    assert r.timestamp is not None


def test_record_attempt_cached_flag():
    r = record_attempt(
        agent="ingest", model="claude-haiku-4-5", tokens_in=0, tokens_out=0, cached=True
    )
    assert r.cached is True
    assert r.cost_usd == 0.0


def test_negative_tokens_rejected():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CostRecord(
            agent="ingest",
            model="claude-haiku-4-5",
            tokens_in=-1,
            tokens_out=0,
            cost_usd=0.0,
        )
