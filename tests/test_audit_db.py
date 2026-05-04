"""audit.db schema + connection contract."""

from __future__ import annotations

from pathlib import Path

from engine.audit.db import connect, init_db


def test_init_db_creates_three_tables(tmp_path: Path) -> None:
    db = tmp_path / "audit.db"
    init_db(db)
    assert db.exists()

    conn = connect(db)
    try:
        names = {
            r["name"]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
    finally:
        conn.close()

    assert {"ingest_history", "cost_records", "audit_events"} <= names


def test_init_db_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "audit.db"
    init_db(db)
    init_db(db)  # second call must not error
    init_db(db)


def test_connect_uses_wal_mode(tmp_path: Path) -> None:
    db = tmp_path / "audit.db"
    init_db(db)
    conn = connect(db)
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()
    assert mode.lower() == "wal"


def test_cost_records_check_constraint_on_cached(tmp_path: Path) -> None:
    """`cached` column accepts only 0 or 1."""
    import sqlite3

    import pytest

    db = tmp_path / "audit.db"
    init_db(db)
    conn = connect(db)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO cost_records (id, agent, model, tokens_in, tokens_out, "
                "cost_usd, cached, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("bad", "ingest", "x", 0, 0, 0.0, 5, "2026-05-04T00:00:00Z"),
            )
    finally:
        conn.close()
