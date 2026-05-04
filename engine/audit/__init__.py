"""Append-only audit DB for the Marginalia engine (design §13.2).

Three tables sit at ``<wiki-root>/.wiki/audit.db``:

- ``ingest_history`` — one row per ingest, summarising the work
- ``cost_records`` — one row per LLM attempt; ``cached=1`` rows reflect
  cache hits where no API call was made
- ``audit_events`` — typed lifecycle events with JSON metadata

The DB is the load-bearing observability surface: NB 12+ retros,
compliance queries, and the dashboard's cost block all read from here.
"""

from __future__ import annotations

from engine.audit.dashboard import DATAVIEW_BLOCKS, generate_dashboard
from engine.audit.db import DDL, connect, init_db
from engine.audit.queries import (
    CostByModel,
    cost_summary,
    daily_cost_breakdown,
    events_by_type,
    last_n_ingests,
)
from engine.audit.writer import AuditWriter

__all__ = [
    "DATAVIEW_BLOCKS",
    "DDL",
    "AuditWriter",
    "CostByModel",
    "connect",
    "cost_summary",
    "daily_cost_breakdown",
    "events_by_type",
    "generate_dashboard",
    "init_db",
    "last_n_ingests",
]
