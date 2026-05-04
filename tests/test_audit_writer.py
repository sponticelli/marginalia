"""AuditWriter contract: round-trip writes + JSON metadata."""

from __future__ import annotations

import json
from pathlib import Path

from engine.audit import AuditWriter, connect
from engine.utils.cost_tracker import record_attempt


def test_record_ingest_round_trip(tmp_path: Path) -> None:
    db = tmp_path / "audit.db"
    with AuditWriter(db) as w:
        rid = w.record_ingest(
            job_id="job-1",
            source_ref="raw/example.md",
            source_hash="a" * 64,
            page_paths=["sources/example", "knowledge/example"],
            tokens_in=1000,
            tokens_out=200,
            cost_usd=0.0042,
            duration_ms=4321,
        )
    assert rid

    conn = connect(db)
    try:
        row = conn.execute("SELECT * FROM ingest_history WHERE id = ?", (rid,)).fetchone()
    finally:
        conn.close()

    assert row["source_ref"] == "raw/example.md"
    assert row["source_hash"] == "a" * 64
    assert json.loads(row["page_paths"]) == ["sources/example", "knowledge/example"]
    assert row["tokens_in"] == 1000
    assert row["cost_usd"] == 0.0042


def test_record_cost_round_trip(tmp_path: Path) -> None:
    db = tmp_path / "audit.db"
    rec = record_attempt(
        agent="ingest",
        model="claude-haiku-4-5",
        tokens_in=400,
        tokens_out=80,
        cached=False,
        job_id="job-1",
    )
    with AuditWriter(db) as w:
        w.record_cost(rec)

    conn = connect(db)
    try:
        row = conn.execute("SELECT * FROM cost_records WHERE id = ?", (rec.id,)).fetchone()
    finally:
        conn.close()

    assert row["agent"] == "ingest"
    assert row["model"] == "claude-haiku-4-5"
    assert row["cached"] == 0  # bool → int


def test_record_cost_cached_persists_as_one(tmp_path: Path) -> None:
    db = tmp_path / "audit.db"
    rec = record_attempt(
        agent="ingest",
        model="claude-haiku-4-5",
        tokens_in=0,
        tokens_out=0,
        cached=True,
    )
    with AuditWriter(db) as w:
        w.record_cost(rec)

    conn = connect(db)
    try:
        row = conn.execute("SELECT cached FROM cost_records WHERE id = ?", (rec.id,)).fetchone()
    finally:
        conn.close()
    assert row["cached"] == 1


def test_record_event_serialises_metadata(tmp_path: Path) -> None:
    db = tmp_path / "audit.db"
    with AuditWriter(db) as w:
        rid = w.record_event(
            event_type="contradiction_found",
            metadata={
                "page_a": "knowledge/decisions/A",
                "page_b": "knowledge/decisions/B",
                "severity": "high",
                "evidence": ["quote 1", "quote 2"],
            },
            job_id="job-2",
        )

    conn = connect(db)
    try:
        row = conn.execute("SELECT * FROM audit_events WHERE id = ?", (rid,)).fetchone()
    finally:
        conn.close()

    meta = json.loads(row["metadata"])
    assert meta["severity"] == "high"
    assert meta["evidence"] == ["quote 1", "quote 2"]
    assert row["event_type"] == "contradiction_found"
    assert row["job_id"] == "job-2"
