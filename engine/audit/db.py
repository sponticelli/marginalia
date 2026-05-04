"""SQLite connection management + schema for the audit DB (design §13.2).

One DB file per wiki at ``<wiki-root>/.wiki/audit.db``. Sits next to
``jobs.db`` and uses the same WAL-mode pattern from
``engine/jobs/db.py`` so it's safe under concurrent worker writes.

Three append-only tables:

- ``ingest_history`` — one row per ingest call (success or failure).
- ``cost_records`` — one row per LLM attempt; ``cached=1`` rows have
  ``tokens_in=tokens_out=0``.
- ``audit_events`` — typed events with JSON metadata (contradictions,
  hook failures, archive moves, schema failures, etc.).

``init_db`` is idempotent (uses ``CREATE TABLE IF NOT EXISTS``); call
it on every worker start.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DDL = """\
CREATE TABLE IF NOT EXISTS ingest_history (
    id           TEXT PRIMARY KEY,
    job_id       TEXT NOT NULL,
    source_ref   TEXT NOT NULL,
    source_hash  TEXT NOT NULL,
    page_paths   TEXT NOT NULL,           -- JSON array
    tokens_in    INTEGER NOT NULL,
    tokens_out   INTEGER NOT NULL,
    cost_usd     REAL    NOT NULL,
    duration_ms  INTEGER NOT NULL,
    timestamp    TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS ingest_history_job_idx       ON ingest_history(job_id);
CREATE INDEX IF NOT EXISTS ingest_history_timestamp_idx ON ingest_history(timestamp);

CREATE TABLE IF NOT EXISTS cost_records (
    id           TEXT PRIMARY KEY,
    job_id       TEXT,
    agent        TEXT    NOT NULL,
    model        TEXT    NOT NULL,
    tokens_in    INTEGER NOT NULL,
    tokens_out   INTEGER NOT NULL,
    cost_usd     REAL    NOT NULL,
    cached       INTEGER NOT NULL CHECK (cached IN (0, 1)),
    timestamp    TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS cost_records_job_idx       ON cost_records(job_id);
CREATE INDEX IF NOT EXISTS cost_records_timestamp_idx ON cost_records(timestamp);
CREATE INDEX IF NOT EXISTS cost_records_agent_idx     ON cost_records(agent);
CREATE INDEX IF NOT EXISTS cost_records_cached_idx    ON cost_records(cached);

CREATE TABLE IF NOT EXISTS audit_events (
    id           TEXT PRIMARY KEY,
    job_id       TEXT,
    event_type   TEXT NOT NULL,
    metadata     TEXT NOT NULL,           -- JSON
    timestamp    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS audit_events_type_idx      ON audit_events(event_type);
CREATE INDEX IF NOT EXISTS audit_events_timestamp_idx ON audit_events(timestamp);
"""


def connect(db_path: Path | str) -> sqlite3.Connection:
    """Open an audit DB connection with WAL + 5s busy timeout.

    Mirrors ``engine.jobs.db.connect`` exactly — same isolation level,
    pragmas, row factory. The audit DB and jobs DB are sibling files
    using sibling connection conventions.
    """
    conn = sqlite3.connect(
        str(db_path),
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db(db_path: Path | str) -> None:
    """Create audit tables + indexes if missing. Idempotent."""
    p = Path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(p)
    try:
        conn.executescript(DDL)
    finally:
        conn.close()


__all__ = ["DDL", "connect", "init_db"]
