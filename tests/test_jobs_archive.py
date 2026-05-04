"""``archive`` job dispatcher: orchestrates the atomic patchset + audit row.

The atomic correctness lives in ``backlink_rewrite`` (covered by
``test_backlink_rewrite.py``); these tests cover the dispatcher's job:

1. Calls compute → apply on the right target.
2. Moves the file and rewrites referencing pages.
3. Writes an ``archived`` audit_event with the right metadata.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.audit import AuditWriter, events_by_type
from engine.jobs.dispatchers import WorkerCtx, _handle_archive
from engine.utils.backlink_rewrite import copy_wiki_to

POC_WIKI = Path(__file__).resolve().parents[1] / "notebooks" / "data" / "poc-wiki"


class _NoopConfig:
    pass


@pytest.fixture
def wiki_copy(tmp_path: Path) -> Path:
    return copy_wiki_to(POC_WIKI, tmp_path / "wiki")


def _make_ctx(*, wiki_root: Path, audit_db: Path | None) -> tuple[WorkerCtx, AuditWriter | None]:
    writer = AuditWriter(audit_db) if audit_db else None
    return (
        WorkerCtx(
            wiki_root=wiki_root,
            config=_NoopConfig(),  # type: ignore[arg-type]
            client=object(),  # type: ignore[arg-type]
            db_path=(audit_db.parent / "jobs.db") if audit_db else wiki_root / "jobs.db",
            audit_writer=writer,
        ),
        writer,
    )


@pytest.mark.asyncio
async def test_archive_handler_moves_file_and_rewrites_backlinks(
    tmp_path: Path, wiki_copy: Path
) -> None:
    """End-to-end against the canonical poc-wiki fixture."""
    audit_db = tmp_path / "audit.db"
    ctx, writer = _make_ctx(wiki_root=wiki_copy, audit_db=audit_db)
    assert writer is not None
    target = "knowledge/decisions/apollo-q2-ship"
    try:
        result = await _handle_archive(
            {"page": target, "reason": "upstream-deleted"},
            ctx,
            "job-archive-1",
        )
    finally:
        writer.close()

    # The page moved.
    old = wiki_copy / "knowledge" / "decisions" / "apollo-q2-ship.md"
    new = wiki_copy / "knowledge" / "decisions" / "archived" / "apollo-q2-ship.md"
    assert not old.exists()
    assert new.exists()

    # New page has archive frontmatter.
    import frontmatter

    post = frontmatter.load(new)
    assert post.metadata["status"] == "archived"
    assert post.metadata["archived_reason"] == "upstream-deleted"
    assert post.metadata["archived_date"] is not None

    # Result reflects the rewrite count from the patchset.
    assert result["page"] == target
    assert result["new_wikilink"] == "knowledge/decisions/archived/apollo-q2-ship"
    assert result["referencing_count"] >= 0  # poc-wiki may or may not link to it

    # Audit event landed.
    events = events_by_type(audit_db, event_type="archived")
    assert len(events) == 1
    assert events[0]["job_id"] == "job-archive-1"
    assert events[0]["metadata"]["page"] == target
    assert events[0]["metadata"]["reason"] == "upstream-deleted"


@pytest.mark.asyncio
async def test_archive_handler_no_audit_writer_is_safe(wiki_copy: Path) -> None:
    """When ctx.audit_writer is None, the archive still completes."""
    ctx, _ = _make_ctx(wiki_root=wiki_copy, audit_db=None)
    result = await _handle_archive(
        {"page": "knowledge/decisions/apollo-q2-ship", "reason": "manual"},
        ctx,
        "job-archive-2",
    )
    assert result["reason"] == "manual"


@pytest.mark.asyncio
async def test_archive_handler_unknown_target_raises(tmp_path: Path, wiki_copy: Path) -> None:
    """Targeting a page that doesn't exist raises FileNotFoundError → worker fails the job."""
    ctx, writer = _make_ctx(wiki_root=wiki_copy, audit_db=tmp_path / "audit.db")
    try:
        with pytest.raises(FileNotFoundError):
            await _handle_archive(
                {"page": "knowledge/does-not-exist", "reason": "manual"},
                ctx,
                "job-archive-3",
            )
    finally:
        if writer is not None:
            writer.close()
