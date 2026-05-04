"""Worker loop tests — retry, dead-letter, fan-out, max_jobs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from engine.jobs import (
    JobStatus,
    children_of,
    connect,
    count_by_status,
    enqueue,
    get_job,
    init_db,
    list_jobs,
    run_worker,
)
from engine.jobs.dispatchers import WorkerCtx


@dataclass
class _NoopConfig:
    """Stand-in MarginaliaConfig — never used because dispatchers bail before
    config-touching code runs in these tests."""


def _ctx(db_path: Path, tmp_path: Path) -> WorkerCtx:
    return WorkerCtx(
        wiki_root=tmp_path / "wiki",
        config=_NoopConfig(),  # type: ignore[arg-type]
        client=object(),  # type: ignore[arg-type]
        db_path=db_path,
    )


@pytest.fixture
def ctx(tmp_path: Path) -> WorkerCtx:
    db = tmp_path / "jobs.db"
    init_db(db)
    return _ctx(db, tmp_path)


@pytest.mark.asyncio
async def test_mock_flaky_retries_then_succeeds(ctx: WorkerCtx) -> None:
    """Job fails on attempts 1+2, succeeds on attempt 3."""
    conn = connect(ctx.db_path)
    try:
        job_id = enqueue(
            conn,
            "_mock_flaky",
            {"fail_until_attempt": 3},
            max_attempts=5,
        )
    finally:
        conn.close()

    counts = await run_worker(
        ctx,
        max_jobs=3,
        backoff_seconds=(0, 0, 0),  # no real waiting in tests
    )

    conn = connect(ctx.db_path)
    try:
        job = get_job(conn, job_id)
    finally:
        conn.close()
    assert job is not None
    assert job.status == JobStatus.SUCCEEDED
    assert job.attempts == 3
    assert counts["succeeded"] == 1
    # Two failures recorded along the way.
    assert counts["pending"] == 2  # mark_failed returned PENDING twice


@pytest.mark.asyncio
async def test_mock_flaky_persistent_failure_dead_letters(ctx: WorkerCtx) -> None:
    """fail_forever → status flips to dead after max_attempts."""
    conn = connect(ctx.db_path)
    try:
        job_id = enqueue(
            conn,
            "_mock_flaky",
            {"fail_forever": True},
            max_attempts=2,
        )
    finally:
        conn.close()

    await run_worker(ctx, max_jobs=2, backoff_seconds=(0, 0, 0))

    conn = connect(ctx.db_path)
    try:
        job = get_job(conn, job_id)
    finally:
        conn.close()
    assert job is not None
    assert job.status == JobStatus.DEAD
    assert job.attempts == 2


@pytest.mark.asyncio
async def test_worker_drains_then_stops(ctx: WorkerCtx) -> None:
    """stop_when_drained=True exits on the first empty poll."""
    conn = connect(ctx.db_path)
    try:
        for _ in range(5):
            enqueue(conn, "_mock_flaky", {"fail_until_attempt": 1})
    finally:
        conn.close()

    counts = await run_worker(
        ctx,
        stop_when_drained=True,
        backoff_seconds=(0, 0, 0),
    )
    assert counts["succeeded"] == 5

    conn = connect(ctx.db_path)
    try:
        c = count_by_status(conn)
    finally:
        conn.close()
    assert c.get("succeeded") == 5


@pytest.mark.asyncio
async def test_worker_honors_max_jobs(ctx: WorkerCtx) -> None:
    """max_jobs caps how many jobs the worker processes before exiting."""
    conn = connect(ctx.db_path)
    try:
        for _ in range(10):
            enqueue(conn, "_mock_flaky", {"fail_until_attempt": 1})
    finally:
        conn.close()

    await run_worker(
        ctx,
        max_jobs=3,
        backoff_seconds=(0, 0, 0),
    )

    conn = connect(ctx.db_path)
    try:
        succeeded = len(list_jobs(conn, status=JobStatus.SUCCEEDED))
        pending = len(list_jobs(conn, status=JobStatus.PENDING))
    finally:
        conn.close()
    assert succeeded == 3
    assert pending == 7


@pytest.mark.asyncio
async def test_ingest_batch_fan_out(ctx: WorkerCtx) -> None:
    """ingest_batch handler enqueues N child ingest jobs with parent_id."""
    conn = connect(ctx.db_path)
    try:
        parent_id = enqueue(
            conn,
            "ingest_batch",
            {"inputs": ["/a", "/b", "/c"], "synthesize_after": True},
        )
    finally:
        conn.close()

    # Run only the parent — children are now enqueued but not run.
    await run_worker(ctx, max_jobs=1, backoff_seconds=(0, 0, 0))

    conn = connect(ctx.db_path)
    try:
        parent = get_job(conn, parent_id)
        kids = children_of(conn, parent_id)
    finally:
        conn.close()

    assert parent is not None
    assert parent.status == JobStatus.SUCCEEDED
    assert parent.result is not None
    assert parent.result["fan_out"] == 3
    assert len(kids) == 4  # 3 ingest + 1 synthesis
    ingest_kids = [k for k in kids if k.kind == "ingest"]
    synth_kids = [k for k in kids if k.kind == "synthesis"]
    assert len(ingest_kids) == 3
    assert len(synth_kids) == 1
    assert all(k.parent_id == parent_id for k in kids)


@pytest.mark.asyncio
async def test_kind_without_dispatcher_is_dead_lettered(ctx: WorkerCtx) -> None:
    """A valid JobKind that no dispatcher has registered for transitions to dead.

    Models the operationally-real case where a job kind ships in the
    enum (so old rows survive deserialization) but its dispatcher has
    been removed or hasn't been implemented yet — e.g. ``scaffold``,
    which is in the literal but not registered until later notebooks.
    """
    from engine.jobs.dispatchers import DISPATCHERS

    # Sanity: 'scaffold' is in JobKind but has no registered dispatcher yet.
    assert "scaffold" not in DISPATCHERS

    conn = connect(ctx.db_path)
    try:
        # max_attempts=1 so the first (and only) failure dead-letters
        # immediately. With a higher cap the worker would (correctly)
        # cycle the row through the retry ladder before giving up.
        job_id = enqueue(conn, "scaffold", {"target": "/whatever"}, max_attempts=1)
    finally:
        conn.close()

    await run_worker(ctx, max_jobs=1, backoff_seconds=(0, 0, 0))

    conn = connect(ctx.db_path)
    try:
        job = get_job(conn, job_id)
    finally:
        conn.close()
    assert job is not None
    assert job.status == JobStatus.DEAD
    assert "no dispatcher" in (job.error or "")
