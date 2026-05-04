"""Read-side queries for ``audit.db``.

Used by ``marginalia audit *`` CLI verbs and by the dashboard
generator. Every query returns plain dicts (not Pydantic models) so
``--json`` output is a one-line dict-to-stdout serialization with no
custom encoders.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from engine.audit.db import connect


@dataclass(frozen=True)
class CostByModel:
    """One row of the ``cost_summary`` aggregation."""

    model: str
    calls: int
    cached_calls: int
    tokens_in: int
    tokens_out: int
    cost_usd: float


def last_n_ingests(db_path: Path, *, limit: int = 10) -> list[dict]:
    """Return the most recent ``ingest_history`` rows as dicts."""
    conn = connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id, job_id, source_ref, source_hash, page_paths, "
            "tokens_in, tokens_out, cost_usd, duration_ms, timestamp "
            "FROM ingest_history "
            "ORDER BY timestamp DESC "
            "LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()

    return [_row_to_ingest_dict(r) for r in rows]


def cost_summary(db_path: Path, *, days: int = 7) -> list[CostByModel]:
    """Aggregate ``cost_records`` over the last N days, grouped by model."""
    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    conn = connect(db_path)
    try:
        rows = conn.execute(
            "SELECT model, "
            "       COUNT(*) AS calls, "
            "       SUM(CASE WHEN cached = 1 THEN 1 ELSE 0 END) AS cached_calls, "
            "       SUM(tokens_in)  AS tokens_in, "
            "       SUM(tokens_out) AS tokens_out, "
            "       SUM(cost_usd)   AS cost_usd "
            "FROM cost_records "
            "WHERE timestamp >= ? "
            "GROUP BY model "
            "ORDER BY cost_usd DESC",
            (cutoff,),
        ).fetchall()
    finally:
        conn.close()

    return [
        CostByModel(
            model=r["model"],
            calls=int(r["calls"] or 0),
            cached_calls=int(r["cached_calls"] or 0),
            tokens_in=int(r["tokens_in"] or 0),
            tokens_out=int(r["tokens_out"] or 0),
            cost_usd=float(r["cost_usd"] or 0.0),
        )
        for r in rows
    ]


def events_by_type(
    db_path: Path,
    *,
    event_type: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Return ``audit_events`` rows; filter by ``event_type`` if provided."""
    conn = connect(db_path)
    try:
        if event_type is None:
            rows = conn.execute(
                "SELECT id, job_id, event_type, metadata, timestamp "
                "FROM audit_events ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, job_id, event_type, metadata, timestamp "
                "FROM audit_events WHERE event_type = ? "
                "ORDER BY timestamp DESC LIMIT ?",
                (event_type, limit),
            ).fetchall()
    finally:
        conn.close()

    return [_row_to_event_dict(r) for r in rows]


def daily_cost_breakdown(db_path: Path, *, days: int = 7) -> list[dict]:
    """Return per-day cost totals for the dashboard's audit-DB block.

    Output rows: ``{"day": "YYYY-MM-DD", "calls": int, "cost_usd": float}``.
    Days with no calls are omitted (the dashboard renders sparse).
    """
    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    conn = connect(db_path)
    try:
        rows = conn.execute(
            "SELECT substr(timestamp, 1, 10) AS day, "
            "       COUNT(*) AS calls, "
            "       SUM(cost_usd) AS cost_usd "
            "FROM cost_records "
            "WHERE timestamp >= ? "
            "GROUP BY day "
            "ORDER BY day DESC",
            (cutoff,),
        ).fetchall()
    finally:
        conn.close()

    return [
        {"day": r["day"], "calls": int(r["calls"] or 0), "cost_usd": float(r["cost_usd"] or 0.0)}
        for r in rows
    ]


def _row_to_ingest_dict(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"],
        "job_id": r["job_id"],
        "source_ref": r["source_ref"],
        "source_hash": r["source_hash"],
        "page_paths": json.loads(r["page_paths"]),
        "tokens_in": int(r["tokens_in"]),
        "tokens_out": int(r["tokens_out"]),
        "cost_usd": float(r["cost_usd"]),
        "duration_ms": int(r["duration_ms"]),
        "timestamp": r["timestamp"],
    }


def _row_to_event_dict(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"],
        "job_id": r["job_id"],
        "event_type": r["event_type"],
        "metadata": json.loads(r["metadata"]),
        "timestamp": r["timestamp"],
    }


__all__ = [
    "CostByModel",
    "cost_summary",
    "daily_cost_breakdown",
    "events_by_type",
    "last_n_ingests",
]
