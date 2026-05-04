"""backlink_rewrite tests — atomicity + reference rewriting."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from engine.models.pages import ArchivedReason
from engine.utils.backlink_rewrite import (
    apply_archive_patchset,
    compute_archive_patchset,
    copy_wiki_to,
    render_patchset_diff,
)
from engine.utils.wiki_walker import walk_wiki

POC_WIKI = Path(__file__).resolve().parents[1] / "notebooks" / "data" / "poc-wiki"


@pytest.fixture
def wiki_copy(tmp_path: Path) -> Path:
    """A throwaway deep-copy of the canonical PoC wiki."""
    return copy_wiki_to(POC_WIKI, tmp_path / "wiki")


def test_compute_finds_target_and_referencing_pages(wiki_copy: Path) -> None:
    ps = compute_archive_patchset(
        wiki_copy,
        "knowledge/decisions/apollo-q2-ship",
        archived_on=date(2026, 5, 3),
        reason=ArchivedReason.UPSTREAM_DELETED,
    )
    assert ps.old_path.name == "apollo-q2-ship.md"
    assert ps.new_path.parts[-2:] == ("archived", "apollo-q2-ship.md")
    assert ps.new_wikilink == "knowledge/decisions/archived/apollo-q2-ship"

    referencing = sorted(p.path.name for p in ps.referencing_patches)
    assert referencing == ["apollo-project.md", "apollo-q2.md", "apollo-q3.md"]


def test_compute_rejects_already_archived(wiki_copy: Path) -> None:
    ps = compute_archive_patchset(
        wiki_copy,
        "knowledge/decisions/apollo-q2-ship",
        archived_on=date(2026, 5, 3),
        reason=ArchivedReason.UPSTREAM_DELETED,
    )
    apply_archive_patchset(ps)

    with pytest.raises(ValueError, match="already under archived"):
        compute_archive_patchset(
            wiki_copy,
            "knowledge/decisions/archived/apollo-q2-ship",
            archived_on=date(2026, 5, 3),
            reason=ArchivedReason.UPSTREAM_DELETED,
        )


def test_compute_raises_on_missing_target(wiki_copy: Path) -> None:
    with pytest.raises(FileNotFoundError):
        compute_archive_patchset(
            wiki_copy,
            "knowledge/decisions/does-not-exist",
            archived_on=date(2026, 5, 3),
            reason=ArchivedReason.UPSTREAM_DELETED,
        )


def test_apply_moves_target_and_rewrites_backlinks(wiki_copy: Path) -> None:
    ps = compute_archive_patchset(
        wiki_copy,
        "knowledge/decisions/apollo-q2-ship",
        archived_on=date(2026, 5, 3),
        reason=ArchivedReason.UPSTREAM_DELETED,
    )
    apply_archive_patchset(ps)

    assert not (wiki_copy / "knowledge/decisions/apollo-q2-ship.md").exists()
    assert (wiki_copy / "knowledge/decisions/archived/apollo-q2-ship.md").exists()

    pages = list(walk_wiki(wiki_copy))
    old_marker = "[[knowledge/decisions/apollo-q2-ship]]"
    for page in pages:
        assert old_marker not in page.body, f"residual body link in {page.path}"
        for field in ("related", "contradicts", "owners"):
            for entry in getattr(page.page, field, []) or []:
                assert entry != old_marker, f"residual {field} entry in {page.path}"


def test_apply_marks_target_archived(wiki_copy: Path) -> None:
    ps = compute_archive_patchset(
        wiki_copy,
        "knowledge/decisions/apollo-q2-ship",
        archived_on=date(2026, 5, 3),
        reason=ArchivedReason.UPSTREAM_DELETED,
    )
    apply_archive_patchset(ps)

    pages = {p.wikilink: p for p in walk_wiki(wiki_copy)}
    archived = pages["knowledge/decisions/archived/apollo-q2-ship"]
    assert archived.page.status.value == "archived"
    assert archived.page.archived_date == date(2026, 5, 3)
    assert archived.page.archived_reason is not None
    assert archived.page.archived_reason.value == "upstream-deleted"


def test_dry_run_does_not_write(wiki_copy: Path) -> None:
    """Computing a patchset must not touch disk."""
    snapshot_before = sorted(p.relative_to(wiki_copy) for p in wiki_copy.rglob("*.md"))
    ps = compute_archive_patchset(
        wiki_copy,
        "knowledge/decisions/apollo-q2-ship",
        archived_on=date(2026, 5, 3),
        reason=ArchivedReason.UPSTREAM_DELETED,
    )
    diff = render_patchset_diff(ps)
    snapshot_after = sorted(p.relative_to(wiki_copy) for p in wiki_copy.rglob("*.md"))

    assert snapshot_before == snapshot_after
    assert "[[knowledge/decisions/apollo-q2-ship]]" in diff
    assert "[[knowledge/decisions/archived/apollo-q2-ship]]" in diff


def test_compute_rejects_invalid_patched_page(wiki_copy: Path) -> None:
    """If a substitution would produce an invalid page, plan must fail."""
    bad = wiki_copy / "knowledge" / "decisions" / "broken-ref.md"
    bad.write_text(
        "---\n"
        "title: Broken Ref\n"
        "type: decision\n"
        "status: active\n"
        "confidence: high\n"
        "created: 2026-04-01\n"
        "last_synced: 2026-04-01\n"
        "owners:\n  - '[[knowledge/entities/marginalia-engine]]'\n"
        "supersedes: not-a-wikilink\n"
        "---\nbody\n",
        encoding="utf-8",
    )
    with pytest.raises(Exception):  # noqa: B017,PT011 — pydantic.ValidationError
        compute_archive_patchset(
            wiki_copy,
            "knowledge/decisions/apollo-q2-ship",
            archived_on=date(2026, 5, 3),
            reason=ArchivedReason.UPSTREAM_DELETED,
        )

    # Original target still on disk — no partial mutations.
    assert (wiki_copy / "knowledge/decisions/apollo-q2-ship.md").exists()
    assert not (wiki_copy / "knowledge/decisions/archived/apollo-q2-ship.md").exists()
