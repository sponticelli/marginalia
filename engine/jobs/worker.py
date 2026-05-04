"""Worker loop: poll → claim → dispatch → transition.

One ``run_worker`` invocation owns its own SQLite connection. Multiple
workers can run against the same DB simultaneously — concurrency is
handled in ``claim_next`` (see ``queue.py`` for the `BEGIN IMMEDIATE
… RETURNING` story).

Note on retry strategy:
We deliberately don't wrap dispatchers in ``tenacity`` decorators.
The retry ladder lives in the **queue**, not in the handler — failed
jobs flip back to ``pending`` with a future ``retry_at``, and any
worker (this one or another) re-claims them on a later poll. This
makes retries *durable* across worker restarts, which an in-process
``tenacity`` retry can't be. ``tenacity``'s value would be in-process
backoff inside a single claim, which doesn't apply here.
"""

from __future__ import annotations

import asyncio
import logging
import traceback

from engine.jobs.db import connect
from engine.jobs.dispatchers import DISPATCHERS, WorkerCtx
from engine.jobs.models import Job, JobStatus
from engine.jobs.queue import (
    DEFAULT_BACKOFF_SECONDS,
    claim_next,
    mark_failed,
    mark_succeeded,
)

logger = logging.getLogger("marginalia.worker")


async def _execute_one(
    job: Job,
    ctx: WorkerCtx,
    *,
    backoff_seconds: tuple[int, ...],
) -> JobStatus:
    """Run one job's handler and transition the queue row.

    Returns the resulting ``JobStatus`` so callers can count outcomes.
    """
    handler = DISPATCHERS.get(job.kind)
    if handler is None:
        conn = connect(ctx.db_path)
        try:
            mark_failed(
                conn,
                job.id,
                f"no dispatcher registered for kind {job.kind!r}",
                backoff_seconds=(0,),
            )
        finally:
            conn.close()
        return JobStatus.DEAD

    try:
        result = await handler(job.payload, ctx, job.id)
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
        conn = connect(ctx.db_path)
        try:
            new_status = mark_failed(conn, job.id, err, backoff_seconds=backoff_seconds)
        finally:
            conn.close()
        logger.warning("job %s (%s) failed → %s", job.id, job.kind, new_status.value)
        return new_status

    conn = connect(ctx.db_path)
    try:
        mark_succeeded(conn, job.id, result)
    finally:
        conn.close()
    logger.info("job %s (%s) succeeded", job.id, job.kind)
    return JobStatus.SUCCEEDED


async def run_worker(
    ctx: WorkerCtx,
    *,
    max_jobs: int | None = None,
    poll_interval: float = 1.0,
    backoff_seconds: tuple[int, ...] = DEFAULT_BACKOFF_SECONDS,
    stop_when_drained: bool = False,
) -> dict[str, int]:
    """Run until stopped.

    Args:
        ctx: Worker context (wiki root, config, client, db path).
        max_jobs: If set, exit after this many jobs claimed. Useful in
            tests and the notebook's "drain probe" cell.
        poll_interval: Seconds to sleep between polls when the queue
            is drained but ``stop_when_drained=False``.
        backoff_seconds: Retry ladder for transient failures. Default
            is the §7.5 ladder (1m, 5m, 30m).
        stop_when_drained: If True, exit only when *no* pending rows
            remain at all — including rows still inside their
            ``retry_at`` backoff window. The worker continues to poll
            (with ``poll_interval`` between attempts) while any
            pending row exists. This is what the notebook wants for
            tractable demos that include a retry ladder.

    Returns a dict of ``{status: count}`` summarizing what the worker
    transitioned during the run.
    """
    counts: dict[str, int] = {s.value: 0 for s in JobStatus}
    claimed = 0

    while True:
        conn = connect(ctx.db_path)
        try:
            job = claim_next(conn)
            n_pending = (
                conn.execute("SELECT COUNT(*) AS n FROM jobs WHERE status='pending'").fetchone()[
                    "n"
                ]
                if job is None
                else None
            )
        finally:
            conn.close()

        if job is None:
            if stop_when_drained and n_pending == 0:
                return counts
            await asyncio.sleep(poll_interval)
            continue

        status = await _execute_one(job, ctx, backoff_seconds=backoff_seconds)
        counts[status.value] += 1
        claimed += 1

        if max_jobs is not None and claimed >= max_jobs:
            return counts


__all__ = ["run_worker"]
