"""Marginalia CLI — entry point for `marginalia` command."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import typer
from rich.console import Console

from engine.cli.audit import app as audit_app
from engine.cli.cache import app as cache_app
from engine.cli.jobs import app as jobs_app

app = typer.Typer(
    name="marginalia",
    help="Marginalia wiki engine CLI.",
    no_args_is_help=True,
)
app.add_typer(jobs_app, name="jobs")
app.add_typer(cache_app, name="cache")
app.add_typer(audit_app, name="audit")
console = Console()


@app.command()
def hello(name: str = typer.Argument("world")) -> None:
    """Smoke test: prove the CLI is wired up."""
    console.print(f"[bold green]Hello, {name}![/bold green] Marginalia engine is alive.")


@app.command()
def version() -> None:
    """Print the engine version."""
    from engine import __version__

    console.print(f"engine v{__version__}")


@app.command()
def worker(
    db: Path = typer.Option(  # noqa: B008
        None,
        "--db",
        help="Path to jobs.db. Default: $WIKI_CONTENT_REPO/.wiki/jobs.db.",
    ),
    wiki_root: Path = typer.Option(  # noqa: B008
        None,
        "--wiki-root",
        help="Wiki root for handlers that write pages. Default: $WIKI_CONTENT_REPO.",
    ),
    max_jobs: int = typer.Option(
        None,
        "--max-jobs",
        help="Exit after N jobs. Default: run until interrupted.",
    ),
    poll_interval: float = typer.Option(
        1.0,
        "--poll-interval",
        help="Seconds to sleep between polls when the queue is drained.",
    ),
    drain: bool = typer.Option(
        False,
        "--drain",
        help="Exit on the first empty poll instead of looping.",
    ),
) -> None:
    """Run the §7.5 queue worker in the foreground.

    The worker uses the Anthropic API for ``ingest`` and ``synthesis``
    jobs — set ``ANTHROPIC_API_KEY`` before invoking. Mock-only kinds
    (``_mock_flaky``) work without it.
    """
    from anthropic import Anthropic

    from engine.audit import AuditWriter
    from engine.audit.db import init_db as init_audit_db
    from engine.hooks import HookDispatcher
    from engine.jobs import WorkerCtx, init_db, run_worker
    from engine.models.wiki_config import MarginaliaConfig

    repo_env = os.environ.get("WIKI_CONTENT_REPO")
    wiki_root = wiki_root or (Path(repo_env) if repo_env else Path.cwd())
    db = db or wiki_root / ".wiki" / "jobs.db"
    init_db(db)

    audit_db_path = wiki_root / ".wiki" / "audit.db"
    init_audit_db(audit_db_path)

    config = MarginaliaConfig.load(wiki_root)
    client = Anthropic()
    audit_writer = AuditWriter(audit_db_path)
    hook_dispatcher = HookDispatcher(config.hook_config, audit_writer=audit_writer)
    ctx = WorkerCtx(
        wiki_root=wiki_root,
        config=config,
        client=client,
        db_path=db,
        audit_writer=audit_writer,
        hook_dispatcher=hook_dispatcher,
    )

    console.print(
        f"[dim]worker started; db=[cyan]{db}[/cyan] wiki_root=[cyan]{wiki_root}[/cyan][/dim]"
    )
    counts = asyncio.run(
        run_worker(
            ctx,
            max_jobs=max_jobs,
            poll_interval=poll_interval,
            stop_when_drained=drain,
        )
    )
    console.print(f"[green]worker stopped:[/green] {counts}")


if __name__ == "__main__":
    app()
