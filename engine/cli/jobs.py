"""CLI commands for the §7.5 job queue.

Mounted at ``marginalia jobs <verb>``. The verbs match design.md §9.6:

    list     [--status <s>]           — pending|running|failed|dead|succeeded
    status   <id>                     — full row detail
    retry    <id>                     — failed|dead → pending
    cancel   [--yes]                  — kill all pending rows
    purge    --older-than <days>      — delete completed records past TTL

Each command opens a short-lived connection against the configured
``--db`` path (default: ``$WIKI_CONTENT_REPO/.wiki/jobs.db``). The DB
file is auto-created on first use via ``init_db``.
"""

from __future__ import annotations

import os
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from engine.jobs import (
    JobStatus,
    cancel_pending,
    connect,
    count_by_status,
    get_job,
    init_db,
    list_jobs,
    purge_older_than,
    requeue,
)

app = typer.Typer(
    name="jobs",
    help="Inspect and manage the §7.5 job queue.",
    no_args_is_help=True,
)
console = Console()


def _default_db_path() -> Path:
    """Resolve the default jobs.db location.

    Priority: explicit --db (handled by callers) → $WIKI_CONTENT_REPO/.wiki/jobs.db
    → ./.wiki/jobs.db (cwd fallback for the PoC). The path is *not*
    created here; callers run ``init_db`` if needed.
    """
    repo = os.environ.get("WIKI_CONTENT_REPO")
    if repo:
        return Path(repo) / ".wiki" / "jobs.db"
    return Path.cwd() / ".wiki" / "jobs.db"


def _ensure_db(db: Path) -> None:
    if not db.exists():
        init_db(db)


@app.command("list")
def jobs_list(
    status: str | None = typer.Option(
        None,
        "--status",
        help="Filter by status: pending, running, succeeded, failed, dead.",
    ),
    limit: int = typer.Option(50, "--limit", help="Max rows to display."),
    db: Path = typer.Option(  # noqa: B008
        None,
        "--db",
        help="Path to jobs.db. Default: $WIKI_CONTENT_REPO/.wiki/jobs.db.",
    ),
) -> None:
    """List queue rows, most recent first."""
    db = db or _default_db_path()
    _ensure_db(db)

    status_enum = JobStatus(status) if status else None

    conn = connect(db)
    try:
        rows = list_jobs(conn, status=status_enum, limit=limit)
    finally:
        conn.close()

    if not rows:
        console.print("[dim](no jobs)[/dim]")
        return

    table = Table(title=f"Jobs ({len(rows)} shown)", show_lines=False)
    table.add_column("id", style="cyan", no_wrap=True)
    table.add_column("kind", style="magenta")
    table.add_column("status")
    table.add_column("attempts", justify="right")
    table.add_column("created")
    table.add_column("error", style="red", overflow="fold")

    for j in rows:
        err_brief = (j.error or "").splitlines()[0][:60] if j.error else ""
        table.add_row(
            j.id[:8],
            j.kind,
            _status_styled(j.status),
            f"{j.attempts}/{j.max_attempts}",
            j.created_at.strftime("%Y-%m-%d %H:%M"),
            err_brief,
        )
    console.print(table)


def _status_styled(s: JobStatus) -> str:
    color = {
        JobStatus.PENDING: "yellow",
        JobStatus.RUNNING: "blue",
        JobStatus.SUCCEEDED: "green",
        JobStatus.FAILED: "red",
        JobStatus.DEAD: "bold red",
    }[s]
    return f"[{color}]{s.value}[/{color}]"


@app.command("status")
def jobs_status(
    job_id: str = typer.Argument(..., help="Job id (full or 8-char prefix)."),
    db: Path = typer.Option(  # noqa: B008
        None, "--db", help="Path to jobs.db."
    ),
) -> None:
    """Detailed view for one job: payload, result, error trace."""
    db = db or _default_db_path()
    _ensure_db(db)

    conn = connect(db)
    try:
        job = _resolve_job_id(conn, job_id)
    finally:
        conn.close()

    if job is None:
        console.print(f"[red]No job matching id prefix '{job_id}'.[/red]")
        raise typer.Exit(code=1)

    console.print(f"[bold]id:[/bold]         {job.id}")
    console.print(f"[bold]kind:[/bold]       {job.kind}")
    console.print(f"[bold]status:[/bold]     {_status_styled(job.status)}")
    console.print(f"[bold]attempts:[/bold]   {job.attempts}/{job.max_attempts}")
    console.print(f"[bold]created:[/bold]    {job.created_at.isoformat()}")
    if job.started_at:
        console.print(f"[bold]started:[/bold]    {job.started_at.isoformat()}")
    if job.completed_at:
        console.print(f"[bold]completed:[/bold]  {job.completed_at.isoformat()}")
    if job.retry_at:
        console.print(f"[bold]retry_at:[/bold]   {job.retry_at.isoformat()}")
    if job.parent_id:
        console.print(f"[bold]parent_id:[/bold]  {job.parent_id}")
    console.print(f"[bold]payload:[/bold]    {job.payload}")
    if job.result is not None:
        console.print(f"[bold]result:[/bold]     {job.result}")
    if job.error:
        console.print("[bold red]error:[/bold red]")
        console.print(job.error)


def _resolve_job_id(conn, job_id_or_prefix: str):
    """Look up a job by full id, or by 8-char prefix if unique."""
    job = get_job(conn, job_id_or_prefix)
    if job is not None:
        return job
    rows = conn.execute(
        "SELECT id FROM jobs WHERE id LIKE ? || '%' LIMIT 2",
        (job_id_or_prefix,),
    ).fetchall()
    if len(rows) == 1:
        return get_job(conn, rows[0]["id"])
    return None


@app.command("retry")
def jobs_retry(
    job_id: str = typer.Argument(..., help="Job id of a failed/dead row."),
    db: Path = typer.Option(  # noqa: B008
        None, "--db", help="Path to jobs.db."
    ),
) -> None:
    """Reset a failed/dead job to pending with attempts=0."""
    db = db or _default_db_path()
    _ensure_db(db)

    conn = connect(db)
    try:
        job = _resolve_job_id(conn, job_id)
        if job is None:
            console.print(f"[red]No job matching '{job_id}'.[/red]")
            raise typer.Exit(code=1)
        if job.status not in (JobStatus.FAILED, JobStatus.DEAD):
            console.print(f"[yellow]Job is {job.status.value!r}, not failed/dead — no-op.[/yellow]")
            return
        requeue(conn, job.id)
    finally:
        conn.close()
    console.print(f"[green]Requeued {job.id[:8]} as pending.[/green]")


@app.command("cancel")
def jobs_cancel(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
    db: Path = typer.Option(  # noqa: B008
        None, "--db", help="Path to jobs.db."
    ),
) -> None:
    """Cancel all pending jobs (mark them dead). Confirms unless --yes."""
    db = db or _default_db_path()
    _ensure_db(db)

    conn = connect(db)
    try:
        counts = count_by_status(conn)
        n_pending = counts.get("pending", 0)
    finally:
        conn.close()

    if n_pending == 0:
        console.print("[dim](no pending jobs)[/dim]")
        return

    if not yes:
        confirm = typer.confirm(f"Cancel {n_pending} pending job(s)?")
        if not confirm:
            console.print("[yellow]aborted.[/yellow]")
            raise typer.Exit(code=1)

    conn = connect(db)
    try:
        n = cancel_pending(conn)
    finally:
        conn.close()
    console.print(f"[green]Cancelled {n} pending job(s).[/green]")


@app.command("purge")
def jobs_purge(
    older_than: int = typer.Option(
        ...,
        "--older-than",
        help="Delete succeeded/dead rows older than N days.",
    ),
    db: Path = typer.Option(  # noqa: B008
        None, "--db", help="Path to jobs.db."
    ),
) -> None:
    """Delete completed/dead records older than --older-than days."""
    db = db or _default_db_path()
    _ensure_db(db)

    conn = connect(db)
    try:
        n = purge_older_than(conn, older_than)
    finally:
        conn.close()
    console.print(f"[green]Deleted {n} row(s) older than {older_than} days.[/green]")


__all__ = ["app"]
