"""Append-only writer for the §13.2 audit DB.

Three methods, one per table. Each method takes either an explicit
record model (``CostRecord``) or kwargs for the simpler tables. Writes
commit immediately — there is no transaction batching at this layer
because audit rows are independent and small.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from engine.audit.db import connect, init_db
from engine.utils.cost_tracker import CostRecord


class AuditWriter:
    """Thin wrapper over the audit DB that emits one row per call.

    Construction opens (or creates) the DB at ``db_path``. The writer
    owns the connection for its lifetime; callers close it via
    ``close()``. The class is safe to share across coroutines on the
    same worker — SQLite WAL mode handles the synchronization.
    """

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        if not self.db_path.exists():
            init_db(self.db_path)
        self._conn = connect(self.db_path)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> AuditWriter:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def record_ingest(
        self,
        *,
        job_id: str,
        source_ref: str,
        source_hash: str,
        page_paths: list[str],
        tokens_in: int,
        tokens_out: int,
        cost_usd: float,
        duration_ms: int,
        timestamp: datetime | None = None,
    ) -> str:
        """Insert one ``ingest_history`` row. Returns the row id."""
        row_id = str(uuid.uuid4())
        ts = (timestamp or datetime.now(UTC)).isoformat()
        self._conn.execute(
            "INSERT INTO ingest_history "
            "(id, job_id, source_ref, source_hash, page_paths, "
            "tokens_in, tokens_out, cost_usd, duration_ms, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row_id,
                job_id,
                source_ref,
                source_hash,
                json.dumps(page_paths),
                tokens_in,
                tokens_out,
                cost_usd,
                duration_ms,
                ts,
            ),
        )
        return row_id

    def record_cost(self, record: CostRecord) -> str:
        """Insert one ``cost_records`` row from a ``CostRecord`` model.

        ``CostRecord`` was already shaped for this table in NB 02; we
        simply unpack its fields. The ``cached`` bool is stored as a
        SQLite integer (0/1) per the CHECK constraint.
        """
        self._conn.execute(
            "INSERT INTO cost_records "
            "(id, job_id, agent, model, tokens_in, tokens_out, "
            "cost_usd, cached, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.id,
                record.job_id,
                record.agent,
                record.model,
                record.tokens_in,
                record.tokens_out,
                record.cost_usd,
                1 if record.cached else 0,
                record.timestamp.isoformat(),
            ),
        )
        return record.id

    def record_event(
        self,
        *,
        event_type: str,
        metadata: dict[str, Any],
        job_id: str | None = None,
        timestamp: datetime | None = None,
    ) -> str:
        """Insert one ``audit_events`` row. Returns the row id."""
        row_id = str(uuid.uuid4())
        ts = (timestamp or datetime.now(UTC)).isoformat()
        self._conn.execute(
            "INSERT INTO audit_events (id, job_id, event_type, metadata, timestamp) "
            "VALUES (?, ?, ?, ?, ?)",
            (row_id, job_id, event_type, json.dumps(metadata, default=str), ts),
        )
        return row_id


__all__ = ["AuditWriter"]
