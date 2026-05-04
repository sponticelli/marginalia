"""SQLite connection management + schema for the §7.5 job queue.

One DB file per wiki at ``<wiki-root>/.wiki/jobs.db``. Connections use
``journal_mode=WAL`` so two worker processes can read concurrently
while one is writing — essential for the §7.5 "two workers, one DB"
deployment.

``init_db`` is idempotent (uses ``CREATE TABLE IF NOT EXISTS``); call
it on every worker start so first-run and resume both work the same
way.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DDL = """\
CREATE TABLE IF NOT EXISTS jobs (
    id           TEXT PRIMARY KEY,
    kind         TEXT NOT NULL,
    payload      TEXT NOT NULL,        -- JSON
    status       TEXT NOT NULL CHECK (status IN ('pending','running','succeeded','failed','dead')),
    attempts     INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    created_at   TEXT NOT NULL,        -- ISO 8601
    started_at   TEXT,
    completed_at TEXT,
    retry_at     TEXT,                 -- earliest time this row may be re-claimed
    error        TEXT,
    result       TEXT,                 -- JSON
    parent_id    TEXT REFERENCES jobs(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS jobs_status_idx ON jobs(status);
CREATE INDEX IF NOT EXISTS jobs_parent_idx ON jobs(parent_id);
CREATE INDEX IF NOT EXISTS jobs_retry_idx ON jobs(retry_at) WHERE retry_at IS NOT NULL;
"""


def connect(db_path: Path | str) -> sqlite3.Connection:
    """Open a connection with WAL mode + foreign keys + 5s busy timeout.

    ``isolation_level=None`` puts us in *autocommit* mode — we manage
    transactions explicitly with ``BEGIN IMMEDIATE`` in ``claim_next``.
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
    """Create the ``jobs`` table + indexes if missing.

    Creates parent directories if needed (the wiki's ``.wiki/`` may
    not exist yet on first run).
    """
    p = Path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(p)
    try:
        conn.executescript(DDL)
    finally:
        conn.close()


__all__ = ["DDL", "connect", "init_db"]
