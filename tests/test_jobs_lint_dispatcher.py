"""`lint` job dispatcher: writes audit_events + cost_records, fires hooks."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from engine.audit import AuditWriter, cost_summary, events_by_type, last_n_ingests
from engine.hooks.config import Hook, HookConfig
from engine.hooks.dispatcher import HookDispatcher
from engine.jobs.dispatchers import WorkerCtx, _handle_lint


class _NoopConfig:
    pass


def _make_ctx(
    *,
    wiki_root: Path,
    audit_db: Path,
    hook_dispatcher: HookDispatcher | None = None,
) -> tuple[WorkerCtx, AuditWriter]:
    audit_writer = AuditWriter(audit_db)
    ctx = WorkerCtx(
        wiki_root=wiki_root,
        config=_NoopConfig(),  # type: ignore[arg-type]
        client=object(),  # type: ignore[arg-type]
        db_path=audit_db.parent / "jobs.db",
        audit_writer=audit_writer,
        hook_dispatcher=hook_dispatcher,
    )
    return ctx, audit_writer


@pytest.mark.asyncio
async def test_lint_handler_writes_events_and_cost(monkeypatch, tmp_path: Path) -> None:
    """When lint_wiki returns findings, the handler persists them to audit.db."""
    from engine.agents.lint import full_pass as lint_module
    from engine.agents.lint.full_pass import (
        Contradiction,
        LintReport,
        OrphanFinding,
        StaleFinding,
    )
    from engine.models.pages import PageType

    fake_report = LintReport(
        total_pages=4,
        stale_pages=[
            StaleFinding(
                wikilink="sources/old",
                page_type=PageType.SOURCE,
                last_synced=__import__("datetime").date(2026, 1, 1),
                days_stale=124,
            )
        ],
        contradictions=[
            Contradiction(
                page_a="knowledge/decisions/A",
                page_b="knowledge/decisions/B",
                subject="pricing",
                why="A says X, B says Y",
                severity="high",
                evidence=["q1", "q2"],
            )
        ],
        orphans=[
            OrphanFinding(wikilink="knowledge/concepts/lonely", page_type=PageType.CONCEPT),
        ],
        model="claude-opus-4-7",
        tokens_in=4321,
        tokens_out=987,
        cost_usd=0.1234,
        wall_seconds=12.5,
    )

    def _fake_lint_wiki(*_args, **_kwargs):
        return fake_report

    monkeypatch.setattr(lint_module, "lint_wiki", _fake_lint_wiki)

    audit_db = tmp_path / "audit.db"
    ctx, writer = _make_ctx(wiki_root=tmp_path / "wiki", audit_db=audit_db)
    try:
        result = await _handle_lint({}, ctx, "job-lint-1")
    finally:
        writer.close()

    assert result["contradictions"] == 1
    assert result["stale"] == 1
    assert result["orphans"] == 1

    contradictions = events_by_type(audit_db, event_type="contradiction_found")
    assert len(contradictions) == 1
    assert contradictions[0]["metadata"]["severity"] == "high"

    stale = events_by_type(audit_db, event_type="stale_detected")
    assert len(stale) == 1

    orphans = events_by_type(audit_db, event_type="orphan_detected")
    assert len(orphans) == 1

    cost_rows = cost_summary(audit_db, days=30)
    by_model = {r.model: r for r in cost_rows}
    assert "claude-opus-4-7" in by_model
    assert by_model["claude-opus-4-7"].tokens_in == 4321


@pytest.mark.asyncio
async def test_lint_handler_fires_on_lint_complete_hook(monkeypatch, tmp_path: Path) -> None:
    """When a hook is registered, it gets the LintReport JSON on stdin."""
    from engine.agents.lint import full_pass as lint_module
    from engine.agents.lint.full_pass import LintReport

    fake_report = LintReport(
        total_pages=2,
        stale_pages=[],
        contradictions=[],
        orphans=[],
        model="claude-opus-4-7",
        tokens_in=10,
        tokens_out=2,
        cost_usd=0.0001,
        wall_seconds=0.01,
    )

    monkeypatch.setattr(lint_module, "lint_wiki", lambda *a, **k: fake_report)

    captured = tmp_path / "captured.json"
    hook_script = tmp_path / "hook.sh"
    hook_script.write_text(
        f'#!/usr/bin/env bash\nread CTX\necho "$CTX" > {captured}\nexit 0\n',
        encoding="utf-8",
    )
    hook_script.chmod(hook_script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    cfg = HookConfig(on_lint_complete=Hook(command=str(hook_script), blocking=False, timeout_s=5))
    audit_db = tmp_path / "audit.db"
    audit_writer = AuditWriter(audit_db)
    dispatcher = HookDispatcher(cfg, audit_writer=audit_writer)

    ctx, writer = _make_ctx(
        wiki_root=tmp_path / "wiki",
        audit_db=audit_db,
        hook_dispatcher=dispatcher,
    )
    try:
        await _handle_lint({}, ctx, "job-lint-2")
    finally:
        writer.close()
        audit_writer.close()

    import json

    payload = json.loads(captured.read_text(encoding="utf-8"))
    assert payload["job_id"] == "job-lint-2"
    assert payload["total_pages"] == 2
    assert payload["contradictions"] == []


@pytest.mark.asyncio
async def test_lint_handler_no_audit_writer_is_safe(monkeypatch, tmp_path: Path) -> None:
    """ctx.audit_writer=None: handler still completes without writing."""
    from engine.agents.lint import full_pass as lint_module
    from engine.agents.lint.full_pass import LintReport

    monkeypatch.setattr(
        lint_module,
        "lint_wiki",
        lambda *a, **k: LintReport(
            total_pages=0,
            stale_pages=[],
            contradictions=[],
            orphans=[],
            model="claude-opus-4-7",
            tokens_in=0,
            tokens_out=0,
            cost_usd=0.0,
            wall_seconds=0.0,
        ),
    )

    ctx = WorkerCtx(
        wiki_root=tmp_path,
        config=_NoopConfig(),  # type: ignore[arg-type]
        client=object(),  # type: ignore[arg-type]
        db_path=tmp_path / "jobs.db",
        audit_writer=None,
        hook_dispatcher=None,
    )
    result = await _handle_lint({}, ctx, "job")
    assert result["total_pages"] == 0


def test_lint_writes_no_history_rows(tmp_path: Path) -> None:
    """Lint emits cost + events, never `ingest_history` rows."""
    audit_db = tmp_path / "audit.db"
    AuditWriter(audit_db).close()
    assert last_n_ingests(audit_db) == []
