"""Queue operations: enqueue, claim, transition.

The load-bearing primitive is ``claim_next``: a single SQL statement
that atomically marks one ``pending`` row as ``running`` and returns
its full contents. The ``BEGIN IMMEDIATE`` + ``UPDATE … RETURNING``
pattern works because:

1. ``BEGIN IMMEDIATE`` acquires the SQLite reserved lock — only one
   writer at a time. WAL mode means readers don't block.
2. The ``UPDATE`` filters by ``status='pending'`` so two concurrent
   transactions targeting the same row see exactly one winner — the
   loser's row hits zero matches and the ``RETURNING`` returns no row.
3. ``RETURNING *`` (SQLite ≥ 3.35, included in Python 3.12+) avoids a
   second round-trip to read the claimed row.

That's the entire concurrency story. No advisory locks, no
coordinator, no leases.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta

from engine.jobs.models import Job, JobKind, JobStatus

# Backoff ladder per §7.5 — minutes since the most recent failure.
DEFAULT_BACKOFF_SECONDS: tuple[int, ...] = (60, 300, 1800)  # 1m, 5m, 30m


def _now() -> datetime:
    return datetime.now(UTC)


def _row_to_job(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"],
        kind=row["kind"],
        payload=json.loads(row["payload"]),
        status=JobStatus(row["status"]),
        attempts=row["attempts"],
        max_attempts=row["max_attempts"],
        created_at=datetime.fromisoformat(row["created_at"]),
        started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
        completed_at=(datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None),
        retry_at=datetime.fromisoformat(row["retry_at"]) if row["retry_at"] else None,
        error=row["error"],
        result=json.loads(row["result"]) if row["result"] else None,
        parent_id=row["parent_id"],
    )


def enqueue(
    conn: sqlite3.Connection,
    kind: JobKind,
    payload: dict,
    *,
    parent_id: str | None = None,
    max_attempts: int = 3,
) -> str:
    """Insert a fresh ``pending`` job. Returns the new job's id."""
    job_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO jobs (id, kind, payload, status, attempts, max_attempts,
                          created_at, parent_id)
        VALUES (?, ?, ?, 'pending', 0, ?, ?, ?)
        """,
        (
            job_id,
            kind,
            json.dumps(payload),
            max_attempts,
            _now().isoformat(),
            parent_id,
        ),
    )
    return job_id


def claim_next(conn: sqlite3.Connection) -> Job | None:
    """Atomically claim one pending job (or one ready-to-retry job).

    A row is claim-eligible iff:
    - ``status='pending'`` AND
    - ``retry_at IS NULL OR retry_at <= now()``

    Returns the full ``Job`` or ``None`` when the queue is empty/all
    pending rows are still in their backoff window.
    """
    now_iso = _now().isoformat()
    conn.execute("BEGIN IMMEDIATE")
    try:
        cur = conn.execute(
            """
            UPDATE jobs
               SET status='running',
                   started_at=?,
                   attempts=attempts+1
             WHERE id = (
                SELECT id FROM jobs
                 WHERE status='pending'
                   AND (retry_at IS NULL OR retry_at <= ?)
                 ORDER BY created_at
                 LIMIT 1
             )
             RETURNING *
            """,
            (now_iso, now_iso),
        )
        row = cur.fetchone()
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return _row_to_job(row) if row else None


def mark_succeeded(
    conn: sqlite3.Connection,
    job_id: str,
    result: dict,
) -> None:
    conn.execute(
        """
        UPDATE jobs
           SET status='succeeded',
               completed_at=?,
               result=?,
               error=NULL,
               retry_at=NULL
         WHERE id=?
        """,
        (_now().isoformat(), json.dumps(result), job_id),
    )


def mark_failed(
    conn: sqlite3.Connection,
    job_id: str,
    error: str,
    *,
    backoff_seconds: tuple[int, ...] = DEFAULT_BACKOFF_SECONDS,
) -> JobStatus:
    """Transition a running job to either ``failed`` (with ``retry_at``
    set) or ``dead`` (terminal) based on attempt count.

    Returns the resulting status so callers can branch.
    """
    row = conn.execute("SELECT attempts, max_attempts FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        raise KeyError(f"no job with id {job_id!r}")

    attempts: int = row["attempts"]
    max_attempts: int = row["max_attempts"]

    if attempts >= max_attempts:
        conn.execute(
            """
            UPDATE jobs
               SET status='dead',
                   completed_at=?,
                   error=?,
                   retry_at=NULL
             WHERE id=?
            """,
            (_now().isoformat(), error, job_id),
        )
        return JobStatus.DEAD

    # attempts already incremented in claim_next; pick the matching
    # backoff slot (clamped to the last bucket).
    idx = min(attempts - 1, len(backoff_seconds) - 1)
    next_retry = _now() + timedelta(seconds=backoff_seconds[idx])
    conn.execute(
        """
        UPDATE jobs
           SET status='pending',
               error=?,
               retry_at=?,
               started_at=NULL
         WHERE id=?
        """,
        (error, next_retry.isoformat(), job_id),
    )
    return JobStatus.PENDING


def requeue(conn: sqlite3.Connection, job_id: str) -> None:
    """Reset a ``failed``/``dead`` job to ``pending`` with attempts=0.

    The CLI's ``marginalia jobs retry <id>`` lands here.
    """
    conn.execute(
        """
        UPDATE jobs
           SET status='pending',
               attempts=0,
               started_at=NULL,
               completed_at=NULL,
               retry_at=NULL,
               error=NULL
         WHERE id=? AND status IN ('failed','dead')
        """,
        (job_id,),
    )


def cancel_pending(conn: sqlite3.Connection) -> int:
    """Mark every ``pending`` row as ``dead``. Returns the count."""
    cur = conn.execute(
        """
        UPDATE jobs
           SET status='dead',
               completed_at=?,
               error='cancelled'
         WHERE status='pending'
        """,
        (_now().isoformat(),),
    )
    return cur.rowcount


def purge_older_than(conn: sqlite3.Connection, days: int) -> int:
    """Delete ``succeeded``/``dead`` rows older than ``days``."""
    cutoff = (_now() - timedelta(days=days)).isoformat()
    cur = conn.execute(
        """
        DELETE FROM jobs
         WHERE status IN ('succeeded','dead')
           AND completed_at < ?
        """,
        (cutoff,),
    )
    return cur.rowcount


def get_job(conn: sqlite3.Connection, job_id: str) -> Job | None:
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return _row_to_job(row) if row else None


def list_jobs(
    conn: sqlite3.Connection,
    *,
    status: JobStatus | None = None,
    limit: int = 100,
) -> list[Job]:
    if status is not None:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE status=? ORDER BY created_at DESC LIMIT ?",
            (status.value, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_row_to_job(r) for r in rows]


def count_by_status(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status").fetchall()
    return {r["status"]: r["n"] for r in rows}


def children_of(conn: sqlite3.Connection, parent_id: str) -> list[Job]:
    rows = conn.execute(
        "SELECT * FROM jobs WHERE parent_id=? ORDER BY created_at",
        (parent_id,),
    ).fetchall()
    return [_row_to_job(r) for r in rows]


__all__ = [
    "DEFAULT_BACKOFF_SECONDS",
    "cancel_pending",
    "children_of",
    "claim_next",
    "count_by_status",
    "enqueue",
    "get_job",
    "list_jobs",
    "mark_failed",
    "mark_succeeded",
    "purge_older_than",
    "requeue",
]
