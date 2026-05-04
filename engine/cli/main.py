"""Marginalia CLI — entry point for `marginalia` command."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer
from rich.console import Console

from engine.cli import capture
from engine.cli.audit import app as audit_app
from engine.cli.cache import app as cache_app
from engine.cli.hooks import app as hooks_app
from engine.cli.jobs import app as jobs_app
from engine.cli.serve import app as serve_app
from engine.cli.wikis import resolve_wiki_root
from engine.cli.wikis_cli import app as wikis_app
from engine.cli.wikis_cli import use as use_command

app = typer.Typer(
    name="marginalia",
    help="Marginalia wiki engine CLI.",
    no_args_is_help=True,
)
app.add_typer(jobs_app, name="jobs")
app.add_typer(cache_app, name="cache")
app.add_typer(audit_app, name="audit")
app.add_typer(hooks_app, name="hooks")
app.add_typer(serve_app, name="serve")
app.add_typer(wikis_app, name="wikis")

# Capture / staging verbs — see engine/cli/capture.py for the implementations.
app.command("add")(capture.add)
app.command("status")(capture.status)
app.command("unstage")(capture.unstage)
app.command("ingest")(capture.ingest)

# Top-level shortcut for the most common multi-wiki op.
app.command("use")(use_command)

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

    wiki_root = resolve_wiki_root(wiki_root)
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


@app.command()
def ask(
    question: str = typer.Argument(..., help="The question to ask the wiki."),
    save: bool = typer.Option(
        False,
        "--save",
        help="Persist the answer as a QA page in the wiki (sources/qa/<slug>.md).",
    ),
    wiki_root: Path = typer.Option(  # noqa: B008
        None,
        "--wiki-root",
        help="Wiki content root. Default: $WIKI_CONTENT_REPO.",
    ),
) -> None:
    """Run the orchestrator + QA subagent against the wiki and print the answer.

    With ``--save``, the answer is filed as a ``QAPage`` so the question
    becomes a permanent wiki asset (design §6.2 row 7) — what makes the
    wiki *compounding* rather than transient chat.
    """
    from anthropic import Anthropic

    from engine.agents.orchestrator.main import run_orchestrator
    from engine.models.wiki_config import MarginaliaConfig

    wiki_root = resolve_wiki_root(wiki_root)
    config = MarginaliaConfig.load(wiki_root)
    client = Anthropic()

    run = asyncio.run(run_orchestrator(question, wiki_root=wiki_root, config=config, client=client))
    console.print(run.final_text or "[dim](no answer text)[/dim]")
    console.print(
        f"\n[dim]cost: ${run.total_cost_usd:.4f} • "
        f"wall: {run.wall_s:.1f}s • subagents: {len(run.subagent_calls)}[/dim]"
    )

    if save:
        path = _save_qa_page(question, run.final_text, wiki_root=wiki_root)
        console.print(f"[green]saved →[/green] {path}")


def _save_qa_page(question: str, answer: str, *, wiki_root: Path) -> Path:
    """Persist a ``QAPage`` derived from a single ask invocation.

    The question becomes the page title; the user's question is also
    recorded as the source so reviewers see provenance. Slug derives
    from the first ~60 chars of the question.
    """
    import re as _re
    from datetime import date as _date

    from engine.models.pages import (
        Confidence,
        PageStatus,
        SourceAuthority,
        SourceKind,
        SourceRef,
    )
    from engine.tools.upsert_page import upsert_page

    slug = _re.sub(r"[^a-z0-9]+", "-", question.lower()).strip("-")[:60] or "untitled"
    today = _date.today()
    fm = {
        "title": question[:120],
        "type": "qa",
        "status": PageStatus.DRAFT.value,
        "created": today.isoformat(),
        "last_synced": today.isoformat(),
        "confidence": Confidence.MEDIUM.value,
        "sources": [
            SourceRef(
                ref=question,
                kind=SourceKind.LOCAL_FILE,
                captured=today,
                authority=SourceAuthority.OFFHAND,
            ).model_dump(mode="json"),
        ],
    }
    body = f"## Question\n\n{question}\n\n## Answer\n\n{answer}\n"
    return upsert_page(f"sources/qa/{slug}", fm, body, wiki_root=wiki_root)


@app.command()
def search(
    query: str = typer.Argument(..., help="Search terms (case-insensitive)."),
    type_filter: str | None = typer.Option(
        None,
        "--type",
        help="Restrict to pages of this PageType (entity, concept, source, decision, …).",
    ),
    limit: int = typer.Option(10, "--limit", help="Max hits to display."),
    wiki_root: Path = typer.Option(  # noqa: B008
        None,
        "--wiki-root",
        help="Wiki content root. Default: $WIKI_CONTENT_REPO.",
    ),
) -> None:
    """Term-overlap search over wiki pages (no LLM call, no API key needed)."""
    from rich.table import Table

    from engine.models.pages import PageType
    from engine.tools.search import search as _search

    wiki_root = resolve_wiki_root(wiki_root)

    type_enum = PageType(type_filter) if type_filter else None
    hits = _search(query, wiki_root=wiki_root, type=type_enum, limit=limit)
    if not hits:
        console.print("[dim](no hits)[/dim]")
        return

    table = Table(title=f"{len(hits)} hit(s) for {query!r}", show_lines=False)
    table.add_column("path", style="cyan", no_wrap=True)
    table.add_column("type", style="magenta")
    table.add_column("title")
    table.add_column("score", justify="right")
    table.add_column("snippet", overflow="fold")

    for hit in hits:
        table.add_row(
            hit.path,
            hit.type.value,
            hit.title,
            f"{hit.score:.3f}",
            hit.snippet,
        )
    console.print(table)


@app.command()
def scaffold(
    target: str = typer.Option(
        "index",
        "--target",
        help="Which meta-page to regenerate: index | purpose | agents | dashboard.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Print the proposed content without writing any file.",
    ),
    apply: bool = typer.Option(
        False,
        "--apply",
        help=(
            "For purpose/agents only: overwrite the real file instead of writing "
            "to <file>.proposed. Always review the proposal first."
        ),
    ),
    wiki_root: Path = typer.Option(  # noqa: B008
        None,
        "--wiki-root",
        help="Wiki content root. Default: $WIKI_CONTENT_REPO.",
    ),
) -> None:
    """Regenerate wiki meta-pages from current state (design §7.1).

    Four targets, each with its own write contract:

    - ``--target index`` — fully derivable from page state. Default
      action writes ``<wiki>/index.md`` directly. ``--dry-run``
      prints only.
    - ``--target purpose`` — human-authored. Default action writes
      ``<wiki>/purpose.md.proposed`` for diff review; pass
      ``--apply`` to overwrite the real file. ``--dry-run`` prints
      only.
    - ``--target agents`` — same recommend-then-confirm contract as
      purpose, surfaces terminology drift between the style guide
      and recent pages.
    - ``--target dashboard`` — deterministic regenerator (no LLM
      call). Always overwrites ``<wiki>/dashboard.md``; ``--apply``
      and ``--dry-run`` are accepted but only ``--dry-run`` has effect
      (skips the write).

    The proposal carries inline ``<!-- DRIFT: ... -->`` /
    ``<!-- EVIDENCE: ... -->`` comments anchoring each suggestion to
    the wiki state that motivated it.
    """
    from engine.agents.scaffold import (
        SUPPORTED_TARGETS,
        scaffold_agents,
        scaffold_dashboard,
        scaffold_index,
        scaffold_purpose,
    )
    from engine.models.wiki_config import MarginaliaConfig

    if target not in SUPPORTED_TARGETS:
        console.print(
            f"[red]target {target!r} is not yet implemented; supported: "
            f"{', '.join(SUPPORTED_TARGETS)}[/red]"
        )
        raise typer.Exit(code=1)

    if dry_run and apply:
        console.print("[red]--dry-run and --apply are mutually exclusive[/red]")
        raise typer.Exit(code=1)
    if apply and target in ("index", "dashboard"):
        console.print(
            f"[yellow]--apply is for purpose/agents ({target} is always written by default); "
            "ignoring.[/yellow]"
        )

    wiki_root = resolve_wiki_root(wiki_root)
    config = MarginaliaConfig.load(wiki_root)
    # Dashboard is deterministic — no Anthropic client needed; lazy-init the
    # others so a missing API key doesn't block `scaffold dashboard`.
    client = None
    if target != "dashboard":
        from anthropic import Anthropic

        client = Anthropic()

    if target == "dashboard":
        write = not dry_run
        result = asyncio.run(scaffold_dashboard(wiki_root, config=config, write=write))
    elif target == "index":
        # Index target preserves its prior contract: --dry-run gates writing.
        write = not dry_run
        result = asyncio.run(scaffold_index(wiki_root, config=config, client=client, write=write))
    elif target == "purpose":
        write = apply and not dry_run
        if dry_run:
            # Dry-run path: produce content via write=False (writes to .proposed),
            # then delete that file so the side effect matches "no write at all".
            result = asyncio.run(
                scaffold_purpose(wiki_root, config=config, client=client, write=False)
            )
            if result.out_path.is_file():
                result.out_path.unlink()
        else:
            result = asyncio.run(
                scaffold_purpose(wiki_root, config=config, client=client, write=write)
            )
    else:  # agents
        write = apply and not dry_run
        if dry_run:
            result = asyncio.run(
                scaffold_agents(wiki_root, config=config, client=client, write=False)
            )
            if result.out_path.is_file():
                result.out_path.unlink()
        else:
            result = asyncio.run(
                scaffold_agents(wiki_root, config=config, client=client, write=write)
            )

    console.print(
        f"[dim]model={result.model} • pages={result.pages_indexed} • "
        f"cost=${result.cost_usd:.4f} • wall={result.wall_seconds:.1f}s[/dim]\n"
    )
    console.print(result.content)
    if dry_run:
        console.print("\n[dim](dry run — no file written)[/dim]")
    else:
        console.print(f"\n[green]wrote →[/green] {result.out_path}")
        if target in ("purpose", "agents") and not apply:
            from engine.agents.scaffold import AGENTS_FILENAME, PURPOSE_FILENAME

            real_name = PURPOSE_FILENAME if target == "purpose" else AGENTS_FILENAME
            console.print(
                f"[dim]Review with: diff {wiki_root / real_name} {result.out_path}\n"
                f"To apply: marginalia scaffold --target {target} --apply[/dim]"
            )


@app.command()
def log(
    n: int = typer.Option(
        20,
        "-n",
        "--limit",
        help="How many recent ingests to show.",
    ),
    write: bool = typer.Option(
        False,
        "--write",
        help="Also write the rendered log to <wiki>/log.md (idempotent — never appended).",
    ),
    wiki_root: Path = typer.Option(  # noqa: B008
        None,
        "--wiki-root",
        help="Wiki content root. Default: active wiki.",
    ),
) -> None:
    """Print the last N ingests for the current wiki, sourced from `audit.db`.

    The log is generated on demand from the audit DB — no separate
    file is maintained, so there's no race with the worker. With
    ``--write``, also materialize ``<wiki>/log.md`` (overwrites any
    prior copy; rerunning produces a byte-identical file).
    """
    from engine.audit.activity_log import format_activity_log, write_activity_log

    wiki_root = resolve_wiki_root(wiki_root)
    audit_db_path = wiki_root / ".wiki" / "audit.db"

    body = format_activity_log(audit_db_path, limit=n)
    console.print(body)

    if write:
        out = write_activity_log(wiki_root, audit_db_path=audit_db_path, limit=n)
        console.print(f"\n[green]wrote →[/green] {out}")


@app.command()
def resync(
    page_path: str = typer.Argument(
        ...,
        help="Wiki page path without .md (e.g., 'sources/youtube/some-talk').",
    ),
    pr: bool = typer.Option(
        True,
        "--pr/--no-pr",
        help="Open a PR for the refreshed page after the ingest succeeds.",
    ),
    db: Path = typer.Option(  # noqa: B008
        None,
        "--db",
        help="Path to jobs.db. Default: <wiki>/.wiki/jobs.db.",
    ),
    wiki_root: Path = typer.Option(  # noqa: B008
        None,
        "--wiki-root",
        help="Wiki content root. Default: active wiki.",
    ),
) -> None:
    """Re-run the original adapter against a page's first SourceRef.

    Pages ingested after Phase 4 carry ``adapter`` on each SourceRef so
    resync routes deterministically. Older pages without the field
    fall back to URL/extension dispatch on the stored ``ref``.

    The actual ingest runs through the queue (a freshly enqueued
    ``ingest`` job) so it inherits the worker's retry, audit, and hook
    plumbing — and a one-shot drain follows so the call feels
    synchronous.
    """
    from engine.cli.capture import _drain_worker, _print_pr_url_for_chain
    from engine.jobs import connect, enqueue, init_db
    from engine.tools.read_page import PageNotFoundError, read_page

    wiki_root = resolve_wiki_root(wiki_root)
    db_path = db or wiki_root / ".wiki" / "jobs.db"
    init_db(db_path)

    try:
        page, _body = read_page(page_path, wiki_root=wiki_root)
    except PageNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    sources = getattr(page, "sources", None) or []
    if not sources:
        console.print(f"[red]page {page_path!r} has no sources to resync from[/red]")
        raise typer.Exit(code=1)

    first = sources[0]
    ref = first.ref
    adapter = getattr(first, "adapter", None)

    if adapter:
        console.print(f"[dim]resyncing via stored adapter [cyan]{adapter}[/cyan] → {ref}[/dim]")
    else:
        console.print("[yellow]no stored adapter — falling back to URL/extension dispatch[/yellow]")

    # Re-run through the queue using the existing ingest chain. The chain
    # already detects URL vs path and dispatches; the only thing we lose
    # by going via "ingest" rather than a custom resync handler is the
    # ability to force a stored adapter that disagrees with the ref.
    # That edge case (a Notion page ID stored as a bare string) is rare
    # enough that we leave it for a follow-up if it actually bites.
    conn = connect(db_path)
    try:
        job_id = enqueue(
            conn,
            "ingest",
            {"input": ref, "hint": f"resync of {page_path}", "open_pr": pr},
        )
    finally:
        conn.close()

    console.print(f"[green]enqueued resync → ingest job [cyan]{job_id[:8]}[/cyan][/green]")
    _drain_worker(db_path, wiki_root)
    if pr:
        _print_pr_url_for_chain(db_path, parent_job_id=job_id)


@app.command()
def lint(
    scope: str | None = typer.Option(
        None,
        "--scope",
        help="Restrict to one detector: stale, contradictions, orphans (default: all).",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Print the report; never write a lint-report.md file.",
    ),
    threshold_days: int = typer.Option(
        14,
        "--threshold-days",
        help="Stale-detection threshold in days.",
    ),
    wiki_root: Path = typer.Option(  # noqa: B008
        None,
        "--wiki-root",
        help="Wiki content root. Default: $WIKI_CONTENT_REPO.",
    ),
) -> None:
    """Synchronous lint pass: prints stale + contradictions + orphans.

    For nightly automation use the queued path (``marginalia jobs`` +
    a worker dispatching the ``lint`` kind); this command is the
    interactive counterpart for spot-checks.

    ``--scope`` short-circuits the LLM-backed contradictions detector
    when only the cheap pure-Python passes (stale / orphans) are
    needed — useful for "is anything urgent before I open a PR?" runs.
    """
    from datetime import date as _date

    from engine.agents.lint.full_pass import (
        detect_orphans,
        detect_stale,
        format_lint_report_md,
        lint_wiki,
    )
    from engine.utils.wiki_walker import walk_wiki

    wiki_root = resolve_wiki_root(wiki_root)

    if scope in ("stale", "orphans"):
        # Cheap pure-Python paths; skip the Opus call entirely.
        pages = list(walk_wiki(wiki_root))
        if scope == "stale":
            findings = detect_stale(pages, threshold_days=threshold_days, today=_date.today())
            if not findings:
                console.print("[green]no stale pages[/green]")
                return
            for f in findings:
                console.print(f"  [yellow]{f.days_stale:>3}d[/yellow] {f.wikilink}")
            return
        findings = detect_orphans(pages)
        if not findings:
            console.print("[green]no orphans[/green]")
            return
        for f in findings:
            console.print(f"  [yellow]orphan[/yellow] {f.wikilink}")
        return

    if scope == "contradictions":
        # No standalone wrapper today; just run lint_wiki and surface that section.
        from anthropic import Anthropic

        report = lint_wiki(wiki_root, client=Anthropic(), threshold_days=threshold_days)
        for c in report.contradictions:
            console.print(f"[red]{c.severity}[/red] {c.subject}: {c.page_a} vs {c.page_b}")
        if not report.contradictions:
            console.print("[green]no contradictions found[/green]")
        return

    # Default: full pass.
    from anthropic import Anthropic

    report = lint_wiki(wiki_root, client=Anthropic(), threshold_days=threshold_days)
    md = format_lint_report_md(report)
    console.print(md)

    if not dry_run:
        out = wiki_root / "lint-report.md"
        out.write_text(md, encoding="utf-8")
        console.print(f"\n[green]wrote →[/green] {out}")


if __name__ == "__main__":
    app()
