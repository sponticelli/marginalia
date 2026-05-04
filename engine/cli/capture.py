"""Capture / staging CLI verbs (design §9.6).

The user-facing capture pipeline:

    marginalia add <path-or-url> [--analyse-only] [--force]
    marginalia status
    marginalia unstage <ref>
    marginalia ingest [-m hint] [--batch <folder>] [--file <manifest>] [--wait]

Each verb is glue over existing engine code — there's no new ingest
logic here. ``add`` writes into ``$WIKI_RAW_PATH`` (the inbox);
``ingest`` enqueues ``ingest`` (or ``ingest_batch``) jobs into the
queue using the same dispatchers the worker already runs.

URL stages produce ``.url`` marker files (a single-line text file
containing the URL) — the actual fetch is deferred to ingest time so
``add`` stays cheap and reversible.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from engine.cli.wikis import DEFAULT_RAW_PATH, resolve_raw_path, resolve_wiki_root
from engine.jobs import connect, enqueue, init_db

# Kept for backward-compat callers; identical to wikis.DEFAULT_RAW_PATH.
DEFAULT_INBOX = DEFAULT_RAW_PATH
URL_MARKER_SUFFIX = ".url"

console = Console()


def _inbox() -> Path:
    """Resolve the staging inbox via the multi-wiki resolver.

    Priority (per ``engine.cli.wikis.resolve_raw_path``): per-wiki
    override in ``wikis.toml`` → ``$WIKI_RAW_PATH`` env var → default
    ``~/wiki-raw``. The directory is created lazily by ``add``.
    """
    return resolve_raw_path()


def _is_url(s: str) -> bool:
    return s.startswith(("http://", "https://"))


def _safe_url_filename(url: str) -> str:
    """Derive a deterministic, filesystem-safe stem from a URL.

    Used when staging a URL so the same URL twice reuses the same
    marker file (idempotent ``add``). Hash-suffixed so collisions
    across hostnames stay unique.
    """
    import hashlib
    from urllib.parse import urlparse

    parsed = urlparse(url)
    host = (parsed.hostname or "url").replace(".", "-")
    digest = hashlib.sha256(url.encode()).hexdigest()[:8]
    return f"{host}-{digest}{URL_MARKER_SUFFIX}"


def _list_inbox(inbox: Path) -> list[Path]:
    if not inbox.exists():
        return []
    return sorted(p for p in inbox.iterdir() if p.is_file())


def _read_url_marker(p: Path) -> str:
    """Pull the URL out of a ``.url`` marker file."""
    return p.read_text(encoding="utf-8").strip()


def _resolve_inbox_target(inbox: Path, ref: str) -> Path | None:
    """Resolve a ref ('add'-style argument or filename) to an inbox file.

    Accepts either a bare filename, a relative path under the inbox, or
    an absolute path that already lives inside the inbox. Returns
    ``None`` if no match exists.
    """
    candidates = [
        inbox / ref,
        Path(ref),
    ]
    for c in candidates:
        if c.is_file():
            try:
                c.resolve().relative_to(inbox.resolve())
            except ValueError:
                return None
            return c
    return None


# ─── add ───────────────────────────────────────────────────────────


class StageError(ValueError):
    """Raised by ``stage_target`` when a target can't be staged.

    Catchable by callers (the serve endpoint, tests) so they can
    return structured errors instead of letting Typer-style ``Exit``
    bubble out of contexts that don't speak Typer.
    """


def stage_target(target: str, *, force: bool = False, inbox: Path | None = None) -> Path:
    """Stage one URL or local file into the inbox; return the inbox path.

    Pure function form of the ``add`` CLI logic — used by both
    ``marginalia add`` and ``marginalia serve``'s ``POST /add``
    handler. Both need the same idempotency guarantees (URL marker
    filenames are deterministic; existing files refuse without
    ``force``) but different surface (CLI prints, serve returns JSON).
    """
    inbox = inbox or _inbox()
    inbox.mkdir(parents=True, exist_ok=True)

    if _is_url(target):
        marker = inbox / _safe_url_filename(target)
        if marker.exists() and not force:
            raise StageError(
                f"{marker.name} already staged for {target!r}; pass force=True to overwrite"
            )
        marker.write_text(target + "\n", encoding="utf-8")
        return marker

    src = Path(target).expanduser()
    if not src.is_file():
        raise StageError(f"no such file: {src}")
    dest = inbox / src.name
    if dest.exists() and not force:
        raise StageError(f"{dest.name} already in inbox; pass force=True to overwrite")
    shutil.copy2(src, dest)
    return dest


def add(
    target: str = typer.Argument(..., help="Local file path or http(s) URL to stage."),
    analyse_only: bool = typer.Option(
        False,
        "--analyse-only",
        help="Run the analyze step against the cache and print results; do not stage.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Overwrite an existing inbox entry.",
    ),
) -> None:
    """Stage a source for ingestion (or preview it via --analyse-only).

    Files are copied into ``$WIKI_RAW_PATH``; URLs become ``.url``
    marker files in the same inbox. ``marginalia ingest`` later
    enqueues a job per item.
    """
    if analyse_only:
        _run_analyse_only(target)
        return

    try:
        staged = stage_target(target, force=force)
    except StageError as exc:
        msg = str(exc)
        if "already staged" in msg or "already in inbox" in msg:
            console.print(f"[yellow]{msg}[/yellow]")
            raise typer.Exit(code=1) from exc
        console.print(f"[red]{msg}[/red]")
        raise typer.Exit(code=1) from exc

    label = "URL" if _is_url(target) else "file"
    console.print(f"[green]staged {label}[/green] → {staged.name}")


def _run_analyse_only(target: str) -> None:
    """``--analyse-only``: extract + analyze a single source, print the result.

    Skips the synthesize step and never writes to the wiki — purely a
    preview pass. Cache hits are free; cache misses run a real Haiku
    call.
    """
    from anthropic import Anthropic

    from engine.agents.ingest.analyze import analyze_source
    from engine.models.pages import SourceKind
    from engine.models.wiki_config import MarginaliaConfig
    from engine.utils.dispatch import extract, extract_url

    wiki_root = resolve_wiki_root()
    config = MarginaliaConfig.load(wiki_root)
    client = Anthropic()

    async def _run() -> None:
        if _is_url(target):
            extracted = await extract_url(target, client=client)
            kind = SourceKind.YOUTUBE_VIDEO  # only YouTube is wired today
        else:
            extracted = extract(Path(target).expanduser(), client=client)
            kind = SourceKind.LOCAL_FILE
        if extracted.failure_reason:
            console.print(f"[red]extract failed: {extracted.failure_reason}[/red]")
            raise typer.Exit(code=1)
        analysis = await analyze_source(extracted.text, kind, config, client=client)
        console.print(f"[bold]proposed_title:[/bold] {analysis.proposed_title}")
        console.print(f"[bold]proposed_type:[/bold]  {analysis.proposed_type.value}")
        console.print(f"[bold]entities:[/bold]       {', '.join(analysis.entities) or '—'}")
        console.print(f"[bold]tags:[/bold]           {', '.join(analysis.proposed_tags) or '—'}")
        console.print(f"[bold]summary:[/bold]        {analysis.summary}")

    asyncio.run(_run())


# ─── status ─────────────────────────────────────────────────────────


def status() -> None:
    """List items currently staged in the inbox."""
    inbox = _inbox()
    items = _list_inbox(inbox)
    if not items:
        console.print(f"[dim](inbox empty: {inbox})[/dim]")
        return

    table = Table(title=f"Staged items ({len(items)} in {inbox})", show_lines=False)
    table.add_column("name", style="cyan")
    table.add_column("kind")
    table.add_column("size", justify="right")
    table.add_column("source", overflow="fold")

    for p in items:
        if p.suffix == URL_MARKER_SUFFIX:
            kind = "url"
            source = _read_url_marker(p)
            size = "—"
        else:
            kind = p.suffix.lstrip(".") or "file"
            source = str(p)
            size = f"{p.stat().st_size:,}"
        table.add_row(p.name, kind, size, source)
    console.print(table)


# ─── unstage ───────────────────────────────────────────────────────


def unstage(
    ref: str = typer.Argument(..., help="Inbox filename or path to remove."),
) -> None:
    """Remove an item from the staging inbox."""
    inbox = _inbox()
    target = _resolve_inbox_target(inbox, ref)
    if target is None:
        console.print(f"[red]No inbox entry matching {ref!r} (inbox: {inbox}).[/red]")
        raise typer.Exit(code=1)
    target.unlink()
    console.print(f"[green]unstaged[/green] {target.name}")


# ─── ingest ─────────────────────────────────────────────────────────


def ingest(
    hint: str | None = typer.Option(
        None,
        "-m",
        "--message",
        help="Synthesis hint passed through to the ingest agent.",
    ),
    batch: Path | None = typer.Option(  # noqa: B008
        None,
        "--batch",
        help="Folder of sources to ingest as a single fan-out batch.",
    ),
    file: Path | None = typer.Option(  # noqa: B008
        None,
        "--file",
        help="Manifest file (one path/URL per line) to ingest as a batch.",
    ),
    wait: bool = typer.Option(
        False,
        "--wait",
        help="Drain a one-shot worker after enqueuing so the call feels synchronous.",
    ),
    pr: bool = typer.Option(
        True,
        "--pr/--no-pr",
        help=(
            "When --wait completes successfully on a single source, open a PR "
            "for the new page. Disabled in batch mode."
        ),
    ),
    db: Path | None = typer.Option(  # noqa: B008
        None,
        "--db",
        help="Path to jobs.db. Default: $WIKI_CONTENT_REPO/.wiki/jobs.db.",
    ),
    wiki_root_opt: Path | None = typer.Option(  # noqa: B008
        None,
        "--wiki-root",
        help="Wiki content root. Default: $WIKI_CONTENT_REPO.",
    ),
) -> None:
    """Enqueue an ``ingest`` (or ``ingest_batch``) job for staged items.

    Sources come from one of: ``--batch <folder>``, ``--file <manifest>``,
    or (default) the staging inbox at ``$WIKI_RAW_PATH``. With ``--wait``,
    spin up a one-shot worker after enqueue and drain — useful for the
    "I just staged one URL, show me the page" flow.
    """
    sources = _resolve_ingest_sources(batch=batch, manifest=file)
    if not sources:
        console.print("[yellow]no sources to ingest[/yellow]")
        raise typer.Exit(code=1)

    wiki_root = resolve_wiki_root(wiki_root_opt)
    db_path = db or wiki_root / ".wiki" / "jobs.db"
    init_db(db_path)

    # Enqueue: single source → ingest; multi → ingest_batch.
    # ``open_pr`` is forwarded into the ingest payload so the dispatcher
    # spawns a child pr_create job after a successful upsert. PR open
    # gets its own retry ladder this way (gh rate limits, network blips
    # don't block ingest from succeeding).
    open_pr_for_ingest = pr and len(sources) == 1
    conn = connect(db_path)
    try:
        if len(sources) == 1:
            job_id = enqueue(
                conn,
                "ingest",
                {"input": sources[0], "hint": hint, "open_pr": open_pr_for_ingest},
            )
            kind_label = "ingest"
        else:
            job_id = enqueue(
                conn,
                "ingest_batch",
                {"inputs": sources, "hint": hint, "synthesize_after": False},
            )
            kind_label = f"ingest_batch (n={len(sources)})"
    finally:
        conn.close()

    console.print(f"[green]enqueued[/green] {kind_label} → job [cyan]{job_id[:8]}[/cyan]")

    if wait:
        _drain_worker(db_path, wiki_root)
        if open_pr_for_ingest:
            _print_pr_url_for_chain(db_path, parent_job_id=job_id)


def _resolve_ingest_sources(
    *,
    batch: Path | None,
    manifest: Path | None,
) -> list[str]:
    """Decide which sources to ingest based on the flag the caller passed.

    Precedence: ``--file`` > ``--batch`` > inbox. Only one is honored
    per call — passing both would be ambiguous, so the manifest wins.
    """
    if manifest is not None:
        if not manifest.is_file():
            console.print(f"[red]manifest not found: {manifest}[/red]")
            raise typer.Exit(code=1)
        lines = [line.strip() for line in manifest.read_text(encoding="utf-8").splitlines()]
        return [line for line in lines if line and not line.startswith("#")]

    if batch is not None:
        if not batch.is_dir():
            console.print(f"[red]batch folder not found: {batch}[/red]")
            raise typer.Exit(code=1)
        return [str(p) for p in sorted(batch.iterdir()) if p.is_file()]

    # Default: pull from the staging inbox.
    items = _list_inbox(_inbox())
    out: list[str] = []
    for p in items:
        if p.suffix == URL_MARKER_SUFFIX:
            out.append(_read_url_marker(p))
        else:
            out.append(str(p))
    return out


def _drain_worker(db_path: Path, wiki_root: Path) -> None:
    """Run a one-shot worker until the queue is drained. Used by ``--wait``."""
    from anthropic import Anthropic

    from engine.audit import AuditWriter
    from engine.audit.db import init_db as init_audit_db
    from engine.hooks import HookDispatcher
    from engine.jobs import WorkerCtx, run_worker
    from engine.models.wiki_config import MarginaliaConfig

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
        db_path=db_path,
        audit_writer=audit_writer,
        hook_dispatcher=hook_dispatcher,
    )
    console.print("[dim]draining worker until queue is empty…[/dim]")
    # `--wait` is interactive — the user is at a terminal, so the
    # 1m/5m/30m default retry ladder would make failures feel like
    # the system is hung. Use a tight ladder (1s/2s/5s) so 3 attempts
    # finish in <10s. Background `marginalia worker` runs keep the
    # design's default ladder for durability.
    counts = asyncio.run(
        run_worker(
            ctx,
            stop_when_drained=True,
            poll_interval=0.2,
            backoff_seconds=(1, 2, 5),
        )
    )
    console.print(f"[green]worker stopped:[/green] {counts}")


def _print_pr_url_for_chain(db_path: Path, *, parent_job_id: str) -> None:
    """After ``--wait`` drains, surface the URL the pr_create child produced.

    The ingest dispatcher enqueues exactly one ``pr_create`` child per
    ingest with ``open_pr=True``. We look up that child by parent_id,
    inspect its terminal state, and print whatever the user needs to
    see — the URL on success, the error on failure. Without this, the
    user has to grep ``marginalia jobs list`` to find the PR.
    """
    from engine.jobs import children_of, connect

    conn = connect(db_path)
    try:
        children = children_of(conn, parent_job_id)
    finally:
        conn.close()

    pr_children = [c for c in children if c.kind == "pr_create"]
    if not pr_children:
        console.print(
            "[yellow]no pr_create child enqueued — ingest may have failed before "
            "reaching the upsert step.[/yellow]"
        )
        return

    pr_job = pr_children[0]
    if pr_job.status.value == "succeeded" and pr_job.result and pr_job.result.get("url"):
        console.print(f"[green]opened PR →[/green] {pr_job.result['url']}")
        return
    if pr_job.status.value in ("failed", "dead"):
        err = (pr_job.error or "").splitlines()[0][:200]
        console.print(
            f"[red]pr_create failed[/red] (job {pr_job.id[:8]}, status={pr_job.status.value}): {err}"
        )
        return
    console.print(
        f"[yellow]pr_create still {pr_job.status.value} (job {pr_job.id[:8]}); "
        "drain the worker again or check `marginalia jobs status`.[/yellow]"
    )


__all__ = ["StageError", "add", "ingest", "stage_target", "status", "unstage"]
