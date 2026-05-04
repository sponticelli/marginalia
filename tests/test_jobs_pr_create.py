"""``pr_create`` job dispatcher: wraps engine/tools/open_pr.py for the queue.

Three scenarios cover the dispatcher contract:

1. Happy path — handler returns ``{"url": ..., "branch": ...}``.
2. ``open_pr`` failure raises through to the worker (queue retry ladder
   handles it). On failure ``open_pr`` writes a ``pr_failed`` audit
   event itself; that's covered in ``test_open_pr.py``.
3. The ingest dispatcher chains a child pr_create job when payload
   carries ``open_pr=True``.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

from engine.audit import AuditWriter
from engine.jobs import connect, init_db
from engine.jobs.dispatchers import (
    WorkerCtx,
    _enqueue_pr_create_for_page,
    _handle_pr_create,
)

# ─── helpers (mirror tests/tools/test_open_pr.py) ────────────────────


def _git(repo: Path, *argv: str) -> None:
    subprocess.run(["git", "-C", str(repo), *argv], capture_output=True, text=True, check=True)


def _make_wiki_with_remote(tmp_path: Path) -> Path:
    bare = tmp_path / "wiki-remote.git"
    subprocess.run(["git", "init", "--bare", "--initial-branch=main", str(bare)], check=True)
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    _git(wiki, "init", "--initial-branch=main")
    _git(wiki, "config", "user.email", "test@example.com")
    _git(wiki, "config", "user.name", "Test")
    (wiki / "README.md").write_text("# wiki\n")
    _git(wiki, "add", "README.md")
    _git(wiki, "commit", "-m", "initial")
    _git(wiki, "remote", "add", "origin", str(bare))
    _git(wiki, "push", "-u", "origin", "main")
    return wiki


def _install_gh_mock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pr_url: str) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    gh = bin_dir / "gh"
    gh.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then echo "gh version 2.0.0"; exit 0; fi\n'
        f'echo "{pr_url}"\n'
        "exit 0\n"
    )
    gh.chmod(gh.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin")


class _NoopConfig:
    pass


def _make_ctx(*, wiki_root: Path, db_path: Path, audit_writer: AuditWriter | None) -> WorkerCtx:
    return WorkerCtx(
        wiki_root=wiki_root,
        config=_NoopConfig(),  # type: ignore[arg-type]
        client=object(),  # type: ignore[arg-type]
        db_path=db_path,
        audit_writer=audit_writer,
    )


# ─── tests ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pr_create_handler_opens_pr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Happy path: handler returns URL + branch from open_pr."""
    _install_gh_mock(tmp_path, monkeypatch, "https://github.com/example/wiki/pull/99")
    wiki = _make_wiki_with_remote(tmp_path)
    page = wiki / "sources" / "x.md"
    page.parent.mkdir()
    page.write_text("# x\n")

    audit_db = tmp_path / "audit.db"
    audit_writer = AuditWriter(audit_db)
    try:
        ctx = _make_ctx(wiki_root=wiki, db_path=tmp_path / "jobs.db", audit_writer=audit_writer)
        result = await _handle_pr_create(
            {
                "title": "ingest: x",
                "body": "Adds x",
                "branch": "agent/ingest-x",
                "files": ["sources/x.md"],
            },
            ctx,
            "job-pr-1",
        )
    finally:
        audit_writer.close()

    assert result["url"] == "https://github.com/example/wiki/pull/99"
    assert result["branch"] == "agent/ingest-x"


@pytest.mark.asyncio
async def test_pr_create_handler_propagates_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """gh failure → OpenPrError raised; worker treats as failed job (retry/dead)."""
    from engine.tools.open_pr import GhFailure

    # gh stub that always fails on `pr create`.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then echo "gh version 2.0.0"; exit 0; fi\n'
        'echo "auth required" >&2\n'
        "exit 1\n"
    )
    gh.chmod(gh.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin")

    wiki = _make_wiki_with_remote(tmp_path)
    page = wiki / "sources" / "y.md"
    page.parent.mkdir()
    page.write_text("# y\n")

    audit_db = tmp_path / "audit.db"
    audit_writer = AuditWriter(audit_db)
    try:
        ctx = _make_ctx(wiki_root=wiki, db_path=tmp_path / "jobs.db", audit_writer=audit_writer)
        with pytest.raises(GhFailure):
            await _handle_pr_create(
                {
                    "title": "ingest: y",
                    "body": "",
                    "branch": "agent/ingest-y",
                    "files": ["sources/y.md"],
                },
                ctx,
                "job-pr-2",
            )
    finally:
        audit_writer.close()

    # open_pr writes the audit event itself; verify it landed.
    import sqlite3

    conn = sqlite3.connect(audit_db)
    rows = conn.execute(
        "SELECT job_id, event_type FROM audit_events WHERE event_type='pr_failed'"
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0][0] == "job-pr-2"


def test_enqueue_pr_create_helper_links_to_parent(tmp_path: Path) -> None:
    """The ingest → pr_create chain uses parent_id so children are findable."""
    from engine.jobs import children_of, enqueue

    db_path = tmp_path / "jobs.db"
    init_db(db_path)
    ctx = _make_ctx(wiki_root=tmp_path / "wiki", db_path=db_path, audit_writer=None)

    # Real parent row first — the jobs table enforces FK on parent_id.
    conn = connect(db_path)
    try:
        parent_id = enqueue(conn, "ingest", {"input": "https://example.com/foo"})
    finally:
        conn.close()

    child_id = _enqueue_pr_create_for_page(
        ctx=ctx,
        parent_job_id=parent_id,
        source_ref="https://example.com/foo",
        result={
            "path": "sources/foo",
            "title": "Foo",
            "status": "active",
        },
    )

    conn = connect(db_path)
    try:
        kids = children_of(conn, parent_id)
    finally:
        conn.close()

    assert len(kids) == 1
    assert kids[0].id == child_id
    assert kids[0].kind == "pr_create"
    assert kids[0].payload["files"] == ["sources/foo.md"]
    assert kids[0].payload["branch"] == "agent/ingest-foo"
    assert "Foo" in kids[0].payload["title"]
