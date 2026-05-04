"""Durable job queue (design §7.5).

Public API:

- ``Job``, ``JobStatus``, ``JobKind`` — Pydantic types for queue rows.
- ``init_db(path)``, ``connect(path)`` — SQLite setup.
- ``enqueue``, ``claim_next``, ``mark_succeeded``, ``mark_failed``,
  ``requeue``, ``cancel_pending``, ``purge_older_than`` — primitives.
- ``list_jobs``, ``get_job``, ``count_by_status``, ``children_of`` —
  read helpers used by the CLI.
- ``run_worker`` — the async poll-claim-execute-transition loop.
- ``DISPATCHERS``, ``WorkerCtx`` — the kind→handler registry.
"""

from engine.jobs.db import DDL, connect, init_db
from engine.jobs.dispatchers import DISPATCHERS, WorkerCtx, register_dispatcher
from engine.jobs.models import TERMINAL_STATES, Job, JobKind, JobStatus
from engine.jobs.queue import (
    DEFAULT_BACKOFF_SECONDS,
    cancel_pending,
    children_of,
    claim_next,
    count_by_status,
    enqueue,
    get_job,
    list_jobs,
    mark_failed,
    mark_succeeded,
    purge_older_than,
    requeue,
)
from engine.jobs.worker import run_worker

__all__ = [
    "DDL",
    "DEFAULT_BACKOFF_SECONDS",
    "DISPATCHERS",
    "Job",
    "JobKind",
    "JobStatus",
    "TERMINAL_STATES",
    "WorkerCtx",
    "cancel_pending",
    "children_of",
    "claim_next",
    "connect",
    "count_by_status",
    "enqueue",
    "get_job",
    "init_db",
    "list_jobs",
    "mark_failed",
    "mark_succeeded",
    "purge_older_than",
    "register_dispatcher",
    "requeue",
    "run_worker",
]
