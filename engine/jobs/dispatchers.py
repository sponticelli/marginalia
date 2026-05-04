"""Job kind → handler registry for the §7.5 worker.

Each handler is an async function with signature:

    async def handle(payload: dict, ctx: WorkerCtx, job_id: str) -> dict

Returning a dict commits the job as ``succeeded`` with that dict in
the ``result`` column. Raising commits the job as ``failed`` (or
``dead`` once attempts are exhausted) with the exception text in the
``error`` column.

Handlers MUST be stateless — the worker is free to retry, run them
concurrently in two processes, or interrupt them. State lives in the
DB (``jobs`` rows) and the wiki (``upsert_page`` outputs), nowhere
else.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from engine.jobs.models import JobKind
from engine.utils.cost_tracker import CostRecord

if TYPE_CHECKING:
    from anthropic import Anthropic

    from engine.audit.writer import AuditWriter
    from engine.hooks.dispatcher import HookDispatcher
    from engine.models.wiki_config import MarginaliaConfig

Handler = Callable[[dict, "WorkerCtx", str], Awaitable[dict]]


@dataclass
class WorkerCtx:
    """Static context the worker passes into every dispatcher.

    Carries the Anthropic client, wiki config, and wiki root. The
    config + client are shared across jobs in a worker run; the wiki
    root tells handlers where to write pages.

    ``audit_writer`` and ``hook_dispatcher`` are optional — when
    ``None``, handlers run without persisting to ``audit.db`` and
    without firing hooks (NB 09 behaviour). NB 11 wires both in.
    """

    wiki_root: Path
    config: MarginaliaConfig
    client: Anthropic
    db_path: Path  # so handlers like ingest_batch can enqueue children
    audit_writer: AuditWriter | None = field(default=None)
    hook_dispatcher: HookDispatcher | None = field(default=None)


# Public registry. Dispatchers register themselves at module import; the
# worker reads from this dict when claiming a job.
DISPATCHERS: dict[JobKind, Handler] = {}


def register_dispatcher(kind: JobKind, handler: Handler) -> None:
    DISPATCHERS[kind] = handler


# ─── handlers ────────────────────────────────────────────────────────


async def _handle_ingest(payload: dict, ctx: WorkerCtx, job_id: str) -> dict:
    """Ingest one source. Payload: {"input": <path-or-url>, "hint": optional}.

    Wraps ``run_ingest_chain`` — the same callable the orchestrator uses.
    Result mirrors what the orchestrator's tool reports, plus the
    audit-relevant fields ``content_sha256`` and ``confidence``.

    Observability side effects (when ``ctx.audit_writer`` and/or
    ``ctx.hook_dispatcher`` are wired):
    - ``cost_records`` row per LLM attempt (analyze + synthesize),
      including cache hits as ``cached=1`` rows.
    - ``ingest_history`` row summarising the call (tokens, cost,
      duration, page paths, source hash).
    - ``on_ingest_complete`` hook fired with the page-level context.
      Blocking hooks raise; the worker treats that as a failed job.
    """
    from engine.agents.ingest import run_ingest_chain

    user_input: str = payload["input"]
    hint = payload.get("hint")

    cost_records: list[CostRecord] = []
    on_cost = _make_on_cost(ctx, job_id, sink=cost_records)

    t0 = time.perf_counter()
    summary, result = await run_ingest_chain(
        user_input,
        config=ctx.config,
        client=ctx.client,
        wiki_root=ctx.wiki_root,
        hint=hint,
        on_cost=on_cost,
    )
    duration_ms = int((time.perf_counter() - t0) * 1000)

    if ctx.audit_writer is not None and "path" in result:
        page_paths = [result["path"]]
        ctx.audit_writer.record_ingest(
            job_id=job_id,
            source_ref=user_input,
            source_hash=result.get("content_sha256", ""),
            page_paths=page_paths,
            tokens_in=sum(r.tokens_in for r in cost_records),
            tokens_out=sum(r.tokens_out for r in cost_records),
            cost_usd=sum(r.cost_usd for r in cost_records),
            duration_ms=duration_ms,
        )

    if ctx.hook_dispatcher is not None and "path" in result:
        ctx.hook_dispatcher.fire(
            "on_ingest_complete",
            {
                "job_id": job_id,
                "source_ref": user_input,
                "page_paths": [result["path"]],
                "status": result.get("status"),
                "confidence": result.get("confidence"),
                "tokens_in": sum(r.tokens_in for r in cost_records),
                "tokens_out": sum(r.tokens_out for r in cost_records),
                "cost_usd": sum(r.cost_usd for r in cost_records),
                "duration_ms": duration_ms,
            },
            job_id=job_id,
        )

    # Optional PR open: enqueue a child pr_create job whose dispatcher
    # owns the git/gh subprocess work. Decoupling lets PR opening retry
    # independently of ingest (gh rate limits, network blips) and lets
    # batches eventually share one PR per page-set rather than one each.
    pr_child_id: str | None = None
    if payload.get("open_pr") and "path" in result:
        pr_child_id = _enqueue_pr_create_for_page(
            ctx=ctx,
            parent_job_id=job_id,
            source_ref=user_input,
            result=result,
        )

    out = {"summary": summary, **result}
    if pr_child_id is not None:
        out["pr_create_job_id"] = pr_child_id
    return out


async def _handle_lint(payload: dict, ctx: WorkerCtx, job_id: str) -> dict:
    """Run a full lint pass against the wiki.

    Payload knobs (all optional):
    - ``threshold_days``: stale-detection threshold (default per
      ``DEFAULT_STALE_THRESHOLD_DAYS``).

    Side effects (when wired):
    - One ``cost_records`` row from the Opus contradiction call.
    - One ``audit_events`` row per contradiction (event_type
      ``contradiction_found``); one per stale page (``stale_detected``);
      one per orphan (``orphan_detected``).
    - ``on_lint_complete`` hook fired with the LintReport JSON.
    """
    from engine.agents.lint.full_pass import (
        DEFAULT_STALE_THRESHOLD_DAYS,
        lint_wiki,
    )

    threshold_days = int(payload.get("threshold_days", DEFAULT_STALE_THRESHOLD_DAYS))

    t0 = time.perf_counter()
    report = lint_wiki(
        ctx.wiki_root,
        client=ctx.client,
        threshold_days=threshold_days,
    )
    duration_ms = int((time.perf_counter() - t0) * 1000)

    if ctx.audit_writer is not None:
        # The Opus call's tokens land as one cost_records row.
        from engine.utils.cost_tracker import record_attempt

        ctx.audit_writer.record_cost(
            record_attempt(
                agent="lint",
                model=report.model,
                tokens_in=report.tokens_in,
                tokens_out=report.tokens_out,
                cached=False,
                job_id=job_id,
            )
        )
        for c in report.contradictions:
            ctx.audit_writer.record_event(
                event_type="contradiction_found",
                metadata=c.model_dump(mode="json"),
                job_id=job_id,
            )
        for s in report.stale_pages:
            ctx.audit_writer.record_event(
                event_type="stale_detected",
                metadata=s.model_dump(mode="json"),
                job_id=job_id,
            )
        for o in report.orphans:
            ctx.audit_writer.record_event(
                event_type="orphan_detected",
                metadata=o.model_dump(mode="json"),
                job_id=job_id,
            )

    if ctx.hook_dispatcher is not None:
        ctx.hook_dispatcher.fire(
            "on_lint_complete",
            {
                "job_id": job_id,
                "total_pages": report.total_pages,
                "contradictions": [c.model_dump(mode="json") for c in report.contradictions],
                "stale_pages": [s.model_dump(mode="json") for s in report.stale_pages],
                "orphans": [o.model_dump(mode="json") for o in report.orphans],
                "tokens_in": report.tokens_in,
                "tokens_out": report.tokens_out,
                "cost_usd": report.cost_usd,
                "duration_ms": duration_ms,
            },
            job_id=job_id,
        )

    return {
        "total_pages": report.total_pages,
        "contradictions": len(report.contradictions),
        "stale": len(report.stale_pages),
        "orphans": len(report.orphans),
        "cost_usd": report.cost_usd,
        "duration_ms": duration_ms,
    }


def _make_on_cost(
    ctx: WorkerCtx,
    job_id: str,
    *,
    sink: list[CostRecord],
) -> Callable[[CostRecord], None]:
    """Build an ``on_cost`` callback that appends to a sink and writes audit.

    The sink lets the handler aggregate (sum tokens, etc.) after the
    chain finishes; the audit-write side-effect persists each record
    immediately so a crash mid-run still leaves a partial trail.
    """

    def _emit(record: CostRecord) -> None:
        record.job_id = job_id
        sink.append(record)
        if ctx.audit_writer is not None:
            ctx.audit_writer.record_cost(record)

    return _emit


async def _handle_synthesis(payload: dict, ctx: WorkerCtx, job_id: str) -> dict:
    """Cross-source synthesis. Payload: {"source_paths": [...], "hint": optional}."""
    import frontmatter

    from engine.agents.synthesis.cross_source import (
        derive_default_path,
        synthesize_cross_source,
    )
    from engine.models.pages import SourcePage
    from engine.tools.upsert_page import upsert_page

    source_paths: list[str] = payload["source_paths"]
    hint = payload.get("hint")
    sources: list[SourcePage] = []
    for sp in source_paths:
        full = ctx.wiki_root / f"{sp}.md"
        post = frontmatter.load(full)
        sources.append(SourcePage(**post.metadata))

    page, body, _log = await synthesize_cross_source(
        sources,
        ctx.config,
        hint=hint,
        sources_paths=source_paths,
        client=ctx.client,
    )
    out_path = derive_default_path(page)
    upsert_page(
        out_path,
        page.model_dump(mode="json", exclude_none=True),
        body,
        wiki_root=ctx.wiki_root,
    )
    return {"path": out_path, "status": page.status.value, "title": page.title}


async def _handle_ingest_batch(payload: dict, ctx: WorkerCtx, job_id: str) -> dict:
    """Fan-out: enqueue N child ingest jobs + one synthesis child gated on them.

    Payload: {"inputs": [...], "synthesize_after": bool, "hint": optional}.

    The handler doesn't *wait* for children — it enqueues them and
    returns immediately. The synthesis child's ``parent_id`` points to
    this batch job; a separate watcher (or the next worker poll) is
    responsible for kicking it off once siblings succeed. For the PoC
    notebook we keep that watcher simple: the synthesis child enters
    ``pending`` immediately but its dispatcher is wrapped to no-op
    until all sibling ingest children land.
    """
    from engine.jobs import connect, enqueue

    inputs: list[str] = payload["inputs"]
    synthesize_after: bool = payload.get("synthesize_after", False)
    hint = payload.get("hint")

    child_ids: list[str] = []
    conn = connect(ctx.db_path)
    try:
        for inp in inputs:
            child_id = enqueue(
                conn,
                "ingest",
                {"input": inp, "hint": hint},
                parent_id=job_id,
            )
            child_ids.append(child_id)

        synth_id: str | None = None
        if synthesize_after:
            # The synthesis dispatcher checks sibling completion before
            # running. Until siblings land, it raises a transient error
            # which the retry ladder absorbs.
            synth_id = enqueue(
                conn,
                "synthesis",
                {
                    "source_paths": [],  # filled in by the gated dispatcher
                    "batch_parent_id": job_id,
                    "hint": hint,
                },
                parent_id=job_id,
            )
    finally:
        conn.close()

    return {
        "ingest_child_ids": child_ids,
        "synthesis_child_id": synth_id,
        "fan_out": len(child_ids),
    }


class _NotReady(Exception):
    """Raised by a synthesis-after-batch handler when siblings aren't done."""


async def _handle_synthesis_gated(payload: dict, ctx: WorkerCtx, job_id: str) -> dict:
    """Synthesis variant that waits on a batch parent's other children.

    If ``payload["batch_parent_id"]`` is set, look up sibling jobs; if
    any sibling is still ``pending``/``running``, raise ``_NotReady``
    (the worker treats it as a retryable error). When all siblings are
    ``succeeded``, collect their result paths and run synthesis on
    them. This is how the §10A scenario's "ingest N → synthesize one
    concept page" lands inside a single fan-out job.
    """
    if not payload.get("batch_parent_id"):
        return await _handle_synthesis(payload, ctx, job_id)

    from engine.jobs import children_of, connect

    conn = connect(ctx.db_path)
    try:
        siblings = [c for c in children_of(conn, payload["batch_parent_id"]) if c.id != job_id]
    finally:
        conn.close()

    pending = [s for s in siblings if s.status.value in ("pending", "running")]
    if pending:
        raise _NotReady(f"{len(pending)} sibling ingest job(s) still in flight")

    failed = [s for s in siblings if s.status.value in ("failed", "dead")]
    if failed:
        raise RuntimeError(f"cannot synthesize: {len(failed)} sibling ingest job(s) failed")

    source_paths = [s.result["path"] for s in siblings if s.result and "path" in s.result]
    payload = {**payload, "source_paths": source_paths}
    return await _handle_synthesis(payload, ctx, job_id)


async def _handle_archive(payload: dict, ctx: WorkerCtx, job_id: str) -> dict:
    """Move a wiki page to its ``archived/`` sibling and rewrite all backlinks.

    Payload shape:
        {
            "page": "knowledge/decisions/foo",   # wikilink (no .md)
            "reason": "upstream-deleted" | "superseded" | "manual",
            # optional:
            "archived_subdir": "archived",       # default
        }

    The atomic plan-then-apply primitives in ``backlink_rewrite``
    already handle correctness; the dispatcher's job is to call them
    from the queue, then emit one ``audit_events`` row per archive so
    the dashboard surfaces what happened.
    """
    from datetime import date as _date

    from engine.models.pages import ArchivedReason
    from engine.utils.backlink_rewrite import (
        apply_archive_patchset,
        compute_archive_patchset,
    )

    target = payload["page"]
    reason = ArchivedReason(payload["reason"])
    archived_subdir = payload.get("archived_subdir", "archived")

    patchset = compute_archive_patchset(
        ctx.wiki_root,
        target,
        archived_on=_date.today(),
        reason=reason,
        archived_subdir=archived_subdir,
    )
    apply_archive_patchset(patchset)

    if ctx.audit_writer is not None:
        ctx.audit_writer.record_event(
            event_type="archived",
            metadata={
                "page": target,
                "new_wikilink": patchset.new_wikilink,
                "reason": reason.value,
                "referencing_pages_rewritten": [
                    str(p.path.relative_to(ctx.wiki_root)) for p in patchset.referencing_patches
                ],
            },
            job_id=job_id,
        )

    return {
        "page": target,
        "new_wikilink": patchset.new_wikilink,
        "reason": reason.value,
        "referencing_count": len(patchset.referencing_patches),
    }


async def _handle_pr_create(payload: dict, ctx: WorkerCtx, job_id: str) -> dict:
    """Open a PR for one or more wiki page changes.

    Payload shape:
        {
            "title": str,
            "body": str,
            "branch": str,
            "files": list[str],   # wiki-relative or absolute, see open_pr
            # optional:
            "base": str,          # default "main"
            "remote": str,        # default "origin"
        }

    Returns ``{"url": str, "branch": str}``. Raises ``OpenPrError`` on
    git/gh failure — the queue's retry ladder will pick it up. Audit
    events for failure are written by ``open_pr`` itself when an
    ``audit_writer`` is wired in ``ctx``.
    """
    from engine.tools.open_pr import open_pr

    pr = open_pr(
        title=payload["title"],
        body=payload["body"],
        branch=payload["branch"],
        files=list(payload["files"]),
        wiki_root=ctx.wiki_root,
        base=payload.get("base", "main"),
        remote=payload.get("remote", "origin"),
        audit_writer=ctx.audit_writer,
        job_id=job_id,
    )
    return {"url": pr.url, "branch": pr.branch}


def _enqueue_pr_create_for_page(
    *,
    ctx: WorkerCtx,
    parent_job_id: str,
    source_ref: str,
    result: dict,
) -> str:
    """Build the pr_create payload for a freshly-ingested page and enqueue it.

    Centralized so the title/body/branch convention stays consistent
    whether the parent is ``ingest`` or ``synthesis`` (Phase 3 may grow
    a synthesis-time PR open too).
    """
    from pathlib import Path as _Path

    from engine.jobs import connect, enqueue

    page_path = result["path"]
    pr_payload = {
        "title": f"ingest: {result.get('title', page_path)}",
        "body": (
            f"Ingested from `{source_ref}` via the queue.\n\n"
            f"- **Page:** `{page_path}`\n"
            f"- **Status:** `{result.get('status', '?')}`\n"
            f"- **Parent job:** `{parent_job_id}`\n"
        ),
        "branch": f"agent/ingest-{_Path(page_path).name}",
        "files": [f"{page_path}.md"],
    }
    conn = connect(ctx.db_path)
    try:
        return enqueue(conn, "pr_create", pr_payload, parent_id=parent_job_id)
    finally:
        conn.close()


async def _handle_mock_flaky(payload: dict, ctx: WorkerCtx, job_id: str) -> dict:
    """Demo handler: deterministically fails on early attempts.

    Payload knobs:
    - ``fail_until_attempt``: succeed once ``attempts >= this`` (default 2).
    - ``fail_forever``: if true, always raises (used for the ``dead`` demo).
    - ``sleep_ms``: optional artificial work duration.
    - ``shared_counter_path``: optional file path that increments per call;
      lets tests assert exact attempt counts.
    """
    import asyncio

    if payload.get("sleep_ms"):
        await asyncio.sleep(payload["sleep_ms"] / 1000)

    if payload.get("shared_counter_path"):
        from pathlib import Path as _P

        cp = _P(payload["shared_counter_path"])
        cp.write_text(str(int(cp.read_text() or "0") + 1) if cp.exists() else "1")

    if payload.get("fail_forever"):
        raise RuntimeError("mock_flaky: fail_forever=True")

    # `attempts` was incremented by claim_next before the dispatcher ran,
    # so attempt N succeeds when fail_until_attempt <= N.
    fail_until = payload.get("fail_until_attempt", 2)

    from engine.jobs import connect, get_job

    conn = connect(ctx.db_path)
    try:
        job = get_job(conn, job_id)
    finally:
        conn.close()
    if job is None:
        raise RuntimeError(f"mock_flaky: job {job_id} disappeared")

    if job.attempts < fail_until:
        raise RuntimeError(f"mock_flaky: scheduled failure on attempt {job.attempts}/{fail_until}")
    return {"attempt_succeeded": job.attempts}


# ─── registration ────────────────────────────────────────────────────


register_dispatcher("ingest", _handle_ingest)
register_dispatcher("ingest_batch", _handle_ingest_batch)
register_dispatcher("synthesis", _handle_synthesis_gated)
register_dispatcher("lint", _handle_lint)
register_dispatcher("pr_create", _handle_pr_create)
register_dispatcher("archive", _handle_archive)
register_dispatcher("_mock_flaky", _handle_mock_flaky)


__all__ = [
    "DISPATCHERS",
    "Handler",
    "WorkerCtx",
    "register_dispatcher",
]
