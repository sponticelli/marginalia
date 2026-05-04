-- Standalone DDL for the §7.5 job queue.
--
-- Mirrors the DDL string in engine/jobs/db.py — re-run safely; uses
-- IF NOT EXISTS for both the table and the indexes. Intended for
-- sqlite3 CLI usage outside the Python engine, e.g.:
--
--     sqlite3 .wiki/jobs.db < scripts/init_jobs_db.sql
--
-- If you edit this file, mirror the change in engine/jobs/db.py:DDL.

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
