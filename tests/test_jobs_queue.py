"""Queue primitive tests — schema, enqueue/claim/transition, requeue."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from engine.jobs import (
    Job,
    JobStatus,
    cancel_pending,
    children_of,
    claim_next,
    connect,
    count_by_status,
    enqueue,
    get_job,
    init_db,
    list_jobs,
    mark_failed,
    mark_succeeded,
    purge_older_than,
    requeue,
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "jobs.db"
    init_db(p)
    return p


def test_init_db_is_idempotent(tmp_path: Path) -> None:
    p = tmp_path / "jobs.db"
    init_db(p)
    init_db(p)  # should not raise; CREATE TABLE IF NOT EXISTS
    conn = connect(p)
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    finally:
        conn.close()
    assert {"id", "kind", "payload", "status", "attempts", "parent_id"} <= cols


def test_enqueue_returns_uuid_and_writes_pending_row(db_path: Path) -> None:
    conn = connect(db_path)
    try:
        job_id = enqueue(conn, "_mock_flaky", {"hello": "world"})
        assert isinstance(job_id, str) and len(job_id) == 36
        row = get_job(conn, job_id)
    finally:
        conn.close()

    assert isinstance(row, Job)
    assert row.status == JobStatus.PENDING
    assert row.attempts == 0
    assert row.payload == {"hello": "world"}


def test_claim_next_transitions_to_running_and_increments_attempts(db_path: Path) -> None:
    conn = connect(db_path)
    try:
        job_id = enqueue(conn, "_mock_flaky", {})
        first = claim_next(conn)
        assert first is not None
        assert first.id == job_id
        assert first.status == JobStatus.RUNNING
        assert first.attempts == 1

        # Second claim returns None — only one pending row, now claimed.
        second = claim_next(conn)
        assert second is None
    finally:
        conn.close()


def test_claim_next_orders_by_created_at(db_path: Path) -> None:
    conn = connect(db_path)
    try:
        # Enqueue three jobs; claim should return them in insertion order.
        ids = [enqueue(conn, "_mock_flaky", {"i": i}) for i in range(3)]
        claimed = []
        for _ in range(3):
            j = claim_next(conn)
            assert j is not None
            claimed.append(j.id)
        assert claimed == ids
    finally:
        conn.close()


def test_mark_succeeded_records_result(db_path: Path) -> None:
    conn = connect(db_path)
    try:
        job_id = enqueue(conn, "_mock_flaky", {})
        claim_next(conn)
        mark_succeeded(conn, job_id, {"answer": 42})
        job = get_job(conn, job_id)
    finally:
        conn.close()
    assert job is not None
    assert job.status == JobStatus.SUCCEEDED
    assert job.result == {"answer": 42}
    assert job.completed_at is not None


def test_mark_failed_under_limit_requeues_with_retry_at(db_path: Path) -> None:
    conn = connect(db_path)
    try:
        job_id = enqueue(conn, "_mock_flaky", {})
        claim_next(conn)  # attempts → 1
        new_status = mark_failed(conn, job_id, "boom", backoff_seconds=(60, 300, 1800))
        assert new_status == JobStatus.PENDING

        job = get_job(conn, job_id)
    finally:
        conn.close()
    assert job is not None
    assert job.status == JobStatus.PENDING
    assert job.error == "boom"
    assert job.retry_at is not None
    # retry_at should be roughly now + 60s (within 5s tolerance for test latency).
    delta = job.retry_at - datetime.now(UTC)
    assert timedelta(seconds=55) <= delta <= timedelta(seconds=65)


def test_mark_failed_at_limit_kills_to_dead(db_path: Path) -> None:
    conn = connect(db_path)
    try:
        job_id = enqueue(conn, "_mock_flaky", {}, max_attempts=2)
        # Burn both attempts.
        claim_next(conn)
        mark_failed(conn, job_id, "first", backoff_seconds=(0,))
        # Re-claim (retry_at is now + 0s, so eligible).
        claim_next(conn)
        new_status = mark_failed(conn, job_id, "second", backoff_seconds=(0,))
        assert new_status == JobStatus.DEAD
        job = get_job(conn, job_id)
    finally:
        conn.close()
    assert job is not None
    assert job.status == JobStatus.DEAD
    assert job.attempts == 2
    assert job.error == "second"


def test_claim_skips_rows_in_backoff_window(db_path: Path) -> None:
    conn = connect(db_path)
    try:
        job_id = enqueue(conn, "_mock_flaky", {})
        claim_next(conn)
        mark_failed(conn, job_id, "wait", backoff_seconds=(60,))
        # retry_at is in the future; claim should return None.
        assert claim_next(conn) is None
    finally:
        conn.close()


def test_requeue_resets_attempts_and_status(db_path: Path) -> None:
    conn = connect(db_path)
    try:
        job_id = enqueue(conn, "_mock_flaky", {}, max_attempts=1)
        claim_next(conn)
        mark_failed(conn, job_id, "perma", backoff_seconds=(0,))
        # Now dead.
        assert get_job(conn, job_id).status == JobStatus.DEAD
        requeue(conn, job_id)
        job = get_job(conn, job_id)
    finally:
        conn.close()
    assert job.status == JobStatus.PENDING
    assert job.attempts == 0
    assert job.error is None


def test_list_jobs_filters_by_status(db_path: Path) -> None:
    conn = connect(db_path)
    try:
        a = enqueue(conn, "_mock_flaky", {"i": 1})
        enqueue(conn, "_mock_flaky", {"i": 2})
        claim_next(conn)
        mark_succeeded(conn, a, {"ok": True})

        succeeded = list_jobs(conn, status=JobStatus.SUCCEEDED)
        pending = list_jobs(conn, status=JobStatus.PENDING)
    finally:
        conn.close()

    assert [j.id for j in succeeded] == [a]
    assert len(pending) == 1


def test_count_by_status(db_path: Path) -> None:
    conn = connect(db_path)
    try:
        # Claim+succeed the first job so we exercise both the running→succeeded
        # transition and the resulting histogram. The remaining three rows
        # stay pending.
        a = enqueue(conn, "_mock_flaky", {})
        for _ in range(3):
            enqueue(conn, "_mock_flaky", {})
        claim_next(conn)  # claims `a` (oldest)
        mark_succeeded(conn, a, {})
        counts = count_by_status(conn)
    finally:
        conn.close()
    assert counts.get("succeeded") == 1
    assert counts.get("pending") == 3


def test_parent_child_linkage(db_path: Path) -> None:
    conn = connect(db_path)
    try:
        parent = enqueue(conn, "ingest_batch", {"inputs": []})
        c1 = enqueue(conn, "ingest", {"input": "/a"}, parent_id=parent)
        c2 = enqueue(conn, "ingest", {"input": "/b"}, parent_id=parent)
        kids = children_of(conn, parent)
    finally:
        conn.close()
    assert {k.id for k in kids} == {c1, c2}


def test_cancel_pending_marks_all_dead(db_path: Path) -> None:
    conn = connect(db_path)
    try:
        for _ in range(3):
            enqueue(conn, "_mock_flaky", {})
        n = cancel_pending(conn)
        counts = count_by_status(conn)
    finally:
        conn.close()
    assert n == 3
    assert counts.get("dead") == 3


def test_purge_older_than(db_path: Path) -> None:
    conn = connect(db_path)
    try:
        a = enqueue(conn, "_mock_flaky", {})
        claim_next(conn)
        mark_succeeded(conn, a, {})
        # Backdate completed_at by 60 days.
        old = (datetime.now(UTC) - timedelta(days=60)).isoformat()
        conn.execute("UPDATE jobs SET completed_at=? WHERE id=?", (old, a))
        deleted = purge_older_than(conn, days=30)
    finally:
        conn.close()
    assert deleted == 1
