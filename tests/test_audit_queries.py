"""Audit DB query helpers — last_n_ingests, cost_summary, events_by_type."""

from __future__ import annotations

from pathlib import Path

from engine.audit import (
    AuditWriter,
    cost_summary,
    daily_cost_breakdown,
    events_by_type,
    last_n_ingests,
)
from engine.utils.cost_tracker import record_attempt


def _seed(db: Path) -> None:
    """Populate a small audit DB for query tests."""
    with AuditWriter(db) as w:
        w.record_ingest(
            job_id="j-1",
            source_ref="raw/a.md",
            source_hash="a" * 64,
            page_paths=["sources/a"],
            tokens_in=1000,
            tokens_out=200,
            cost_usd=0.001,
            duration_ms=100,
        )
        w.record_ingest(
            job_id="j-2",
            source_ref="raw/b.md",
            source_hash="b" * 64,
            page_paths=["sources/b"],
            tokens_in=500,
            tokens_out=100,
            cost_usd=0.0005,
            duration_ms=80,
        )
        for cached in (False, False, True):
            w.record_cost(
                record_attempt(
                    agent="ingest",
                    model="claude-haiku-4-5",
                    tokens_in=0 if cached else 100,
                    tokens_out=0 if cached else 20,
                    cached=cached,
                )
            )
        w.record_cost(
            record_attempt(
                agent="synthesis",
                model="claude-sonnet-4-6",
                tokens_in=400,
                tokens_out=120,
                cached=False,
            )
        )
        w.record_event(event_type="contradiction_found", metadata={"sev": "high"})
        w.record_event(event_type="contradiction_found", metadata={"sev": "low"})
        w.record_event(event_type="orphan_detected", metadata={"wikilink": "x"})


def test_last_n_ingests_returns_most_recent_first(tmp_path: Path) -> None:
    db = tmp_path / "audit.db"
    _seed(db)
    rows = last_n_ingests(db, limit=5)
    assert len(rows) == 2
    assert rows[0]["source_ref"] == "raw/b.md"  # most recent (inserted last)
    assert rows[0]["page_paths"] == ["sources/b"]


def test_cost_summary_aggregates_by_model(tmp_path: Path) -> None:
    db = tmp_path / "audit.db"
    _seed(db)
    rows = cost_summary(db, days=30)
    by_model = {r.model: r for r in rows}

    haiku = by_model["claude-haiku-4-5"]
    assert haiku.calls == 3
    assert haiku.cached_calls == 1
    assert haiku.tokens_in == 200  # two non-cached × 100

    sonnet = by_model["claude-sonnet-4-6"]
    assert sonnet.calls == 1


def test_events_by_type_filters(tmp_path: Path) -> None:
    db = tmp_path / "audit.db"
    _seed(db)
    contradictions = events_by_type(db, event_type="contradiction_found")
    assert len(contradictions) == 2
    assert all(r["event_type"] == "contradiction_found" for r in contradictions)

    all_events = events_by_type(db)
    assert len(all_events) == 3


def test_daily_cost_breakdown_returns_dicts(tmp_path: Path) -> None:
    db = tmp_path / "audit.db"
    _seed(db)
    rows = daily_cost_breakdown(db, days=30)
    assert len(rows) >= 1
    assert {"day", "calls", "cost_usd"} <= set(rows[0].keys())
