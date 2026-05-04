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

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from engine.jobs.models import JobKind

if TYPE_CHECKING:
    from anthropic import Anthropic

    from engine.models.wiki_config import MarginaliaConfig

Handler = Callable[[dict, "WorkerCtx", str], Awaitable[dict]]


@dataclass
class WorkerCtx:
    """Static context the worker passes into every dispatcher.

    Carries the Anthropic client, wiki config, and wiki root. The
    config + client are shared across jobs in a worker run; the wiki
    root tells handlers where to write pages.
    """

    wiki_root: Path
    config: MarginaliaConfig
    client: Anthropic
    db_path: Path  # so handlers like ingest_batch can enqueue children


# Public registry. Dispatchers register themselves at module import; the
# worker reads from this dict when claiming a job.
DISPATCHERS: dict[JobKind, Handler] = {}


def register_dispatcher(kind: JobKind, handler: Handler) -> None:
    DISPATCHERS[kind] = handler


# ─── handlers ────────────────────────────────────────────────────────


async def _handle_ingest(payload: dict, ctx: WorkerCtx, job_id: str) -> dict:
    """Ingest one source. Payload: {"input": <path-or-url>, "hint": optional}.

    Wraps ``run_ingest_chain`` — the same callable the orchestrator uses.
    Result mirrors what the orchestrator's tool reports.
    """
    from engine.agents.ingest import run_ingest_chain

    user_input: str = payload["input"]
    hint = payload.get("hint")
    summary, result = await run_ingest_chain(
        user_input,
        config=ctx.config,
        client=ctx.client,
        wiki_root=ctx.wiki_root,
        hint=hint,
    )
    return {"summary": summary, **result}


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
register_dispatcher("_mock_flaky", _handle_mock_flaky)


__all__ = [
    "DISPATCHERS",
    "Handler",
    "WorkerCtx",
    "register_dispatcher",
]
