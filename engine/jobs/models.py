"""Pydantic models + enums for the §7.5 job queue.

The ``Job`` model maps 1:1 to the ``jobs`` table in
``<wiki-root>/.wiki/jobs.db``. Columns ``payload`` and ``result`` are
stored as JSON text in SQLite; ``Job.model_dump_json()`` produces the
write side and ``Job.model_validate_json()`` the read side.

The ``JobStatus`` state machine (per design §7.5):

    pending → running → succeeded
                      ↘ failed → (retry) → running
                                ↘ dead (after max_attempts)
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

JobKind = Literal[
    "ingest",
    "ingest_batch",
    "synthesis",
    "lint",
    "scaffold",
    "resync",
    "pr_create",
    "archive",
    "_mock_flaky",  # demo-only; produces deterministic failures for retry tests
]


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEAD = "dead"


TERMINAL_STATES: frozenset[JobStatus] = frozenset({JobStatus.SUCCEEDED, JobStatus.DEAD})


class Job(BaseModel):
    """One row in the ``jobs`` table.

    ``payload`` and ``result`` are free-form dicts — the dispatcher for
    each ``kind`` defines its own contract for what shape they take.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    kind: JobKind
    payload: dict = Field(default_factory=dict)
    status: JobStatus = JobStatus.PENDING
    attempts: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=3, ge=1)
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    retry_at: datetime | None = None
    error: str | None = None
    result: dict | None = None
    parent_id: str | None = None


__all__ = [
    "TERMINAL_STATES",
    "Job",
    "JobKind",
    "JobStatus",
]
