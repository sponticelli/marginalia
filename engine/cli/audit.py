"""CLI commands for the §13.2 audit DB.

Mounted at ``marginalia audit <verb>``:

    history  [--limit N] [--json]    — last N ingests
    cost     [--days N]  [--json]    — rolling cost summary by model
    events   [--type T]  [--json]    — audit_events filter

Each command resolves the audit-DB path in the same order as
``marginalia jobs``: ``--db`` flag → ``$WIKI_CONTENT_REPO/.wiki/audit.db``
→ ``./.wiki/audit.db``.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from engine.audit import (
    cost_summary,
    events_by_type,
    init_db,
    last_n_ingests,
)

app = typer.Typer(
    name="audit",
    help="Inspect the §13.2 audit DB (ingest history, cost records, events).",
    no_args_is_help=True,
)
console = Console()


def _default_db_path() -> Path:
    """Mirrors `engine.cli.jobs._default_db_path` but for `audit.db`."""
    repo = os.environ.get("WIKI_CONTENT_REPO")
    if repo:
        return Path(repo) / ".wiki" / "audit.db"
    return Path.cwd() / ".wiki" / "audit.db"


def _ensure_db(db: Path) -> None:
    if not db.exists():
        init_db(db)


def _emit_json(rows: list[dict]) -> None:
    """Print one JSON object per line — friendly to `jq` and `head`."""
    for row in rows:
        sys.stdout.write(json.dumps(row, default=str))
        sys.stdout.write("\n")


@app.command("history")
def audit_history(
    limit: int = typer.Option(10, "--limit", help="Max rows to show."),
    db: Path = typer.Option(  # noqa: B008
        None,
        "--db",
        help="Path to audit.db. Default: $WIKI_CONTENT_REPO/.wiki/audit.db.",
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON, one row per line."),
) -> None:
    """Show the most recent ingest_history rows."""
    db = db or _default_db_path()
    _ensure_db(db)
    rows = last_n_ingests(db, limit=limit)

    if not rows:
        if json_out:
            return
        console.print("[dim](no ingest history)[/dim]")
        return

    if json_out:
        _emit_json(rows)
        return

    table = Table(title=f"Ingest history ({len(rows)} shown)")
    table.add_column("when")
    table.add_column("source", overflow="fold")
    table.add_column("page", overflow="fold")
    table.add_column("tokens", justify="right")
    table.add_column("cost", justify="right")
    table.add_column("ms", justify="right")

    for row in rows:
        pages = row["page_paths"]
        page_str = pages[0] if len(pages) == 1 else f"{pages[0]} (+{len(pages) - 1})"
        table.add_row(
            row["timestamp"][:19],
            row["source_ref"],
            page_str,
            f"{row['tokens_in']}/{row['tokens_out']}",
            f"${row['cost_usd']:.4f}",
            str(row["duration_ms"]),
        )
    console.print(table)


@app.command("cost")
def audit_cost(
    days: int = typer.Option(7, "--days", help="Aggregation window in days."),
    db: Path = typer.Option(  # noqa: B008
        None, "--db", help="Path to audit.db."
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON, one row per line."),
) -> None:
    """Aggregate cost_records by model over the last --days days."""
    db = db or _default_db_path()
    _ensure_db(db)
    rows = cost_summary(db, days=days)

    if not rows:
        if json_out:
            return
        console.print(f"[dim](no cost records in the last {days}d)[/dim]")
        return

    if json_out:
        _emit_json(
            [
                {
                    "model": r.model,
                    "calls": r.calls,
                    "cached_calls": r.cached_calls,
                    "tokens_in": r.tokens_in,
                    "tokens_out": r.tokens_out,
                    "cost_usd": r.cost_usd,
                }
                for r in rows
            ]
        )
        return

    table = Table(title=f"Cost summary — last {days}d")
    table.add_column("model", style="cyan")
    table.add_column("calls", justify="right")
    table.add_column("cached", justify="right")
    table.add_column("tokens_in", justify="right")
    table.add_column("tokens_out", justify="right")
    table.add_column("cost", justify="right")

    total_cost = 0.0
    for r in rows:
        table.add_row(
            r.model,
            str(r.calls),
            f"{r.cached_calls}/{r.calls}",
            f"{r.tokens_in:,}",
            f"{r.tokens_out:,}",
            f"${r.cost_usd:.4f}",
        )
        total_cost += r.cost_usd
    console.print(table)
    console.print(f"[dim]total: ${total_cost:.4f} across {sum(r.calls for r in rows)} calls[/dim]")


@app.command("events")
def audit_events(
    event_type: str = typer.Option(
        None,
        "--type",
        help="Filter by event_type (e.g. contradiction_found, hook_failed).",
    ),
    limit: int = typer.Option(50, "--limit", help="Max rows."),
    db: Path = typer.Option(  # noqa: B008
        None, "--db", help="Path to audit.db."
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON, one row per line."),
) -> None:
    """Show audit_events rows; filter via --type."""
    db = db or _default_db_path()
    _ensure_db(db)
    rows = events_by_type(db, event_type=event_type, limit=limit)

    if not rows:
        if json_out:
            return
        suffix = f" of type {event_type!r}" if event_type else ""
        console.print(f"[dim](no events{suffix})[/dim]")
        return

    if json_out:
        _emit_json(rows)
        return

    table = Table(title=f"Audit events ({len(rows)} shown)")
    table.add_column("when")
    table.add_column("type", style="magenta")
    table.add_column("metadata", overflow="fold")

    for row in rows:
        ts = row["timestamp"][:19]
        meta_brief = json.dumps(row["metadata"], default=str)[:120]
        table.add_row(ts, row["event_type"], meta_brief)
    console.print(table)


__all__ = ["app"]
