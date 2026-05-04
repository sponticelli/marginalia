"""wiki_walker tests — page enumeration + skip rules."""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.models.pages import PageType
from engine.utils.wiki_walker import walk_wiki

POC_WIKI = Path(__file__).resolve().parents[1] / "notebooks" / "data" / "poc-wiki"


def test_walk_yields_every_validated_page() -> None:
    """The committed PoC wiki has 5 sources + 7 knowledge pages."""
    pages = list(walk_wiki(POC_WIKI))
    paths = sorted(p.wikilink for p in pages)
    types = {p.page_type for p in pages}

    assert len(pages) == 12
    assert types == {PageType.SOURCE, PageType.DECISION, PageType.CONCEPT, PageType.ENTITY}
    assert paths == [
        "knowledge/concepts/ingest-pipeline",
        "knowledge/concepts/two-step-ingest-cacheability",
        "knowledge/decisions/apollo-q2-ship",
        "knowledge/decisions/apollo-q3-postpone",
        "knowledge/decisions/engine-model-ladder",
        "knowledge/entities/apollo-project",
        "knowledge/entities/marginalia-engine",
        "sources/apollo-launch-blockers-sync",
        "sources/apollo-q2",
        "sources/apollo-q3",
        "sources/marginalia-engine-architecture-and-model-selection",
        "sources/marginalia-ingest-pipeline-architecture",
    ]


def test_walk_skips_raw_inbox(tmp_path: Path) -> None:
    """Files under raw/ must not be yielded (they're inbox content, not pages)."""
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw" / "drop.md").write_text(
        "---\ntitle: drop\ntype: source\nstatus: active\n"
        "created: 2026-05-03\nlast_synced: 2026-05-03\n"
        "sources:\n- ref: x\n  kind: local_file\n  captured: 2026-05-03\n  authority: canonical\n"
        "---\nbody\n",
        encoding="utf-8",
    )
    pages = list(walk_wiki(tmp_path))
    assert pages == []


def test_walk_skips_non_pages_silently(tmp_path: Path) -> None:
    """README.md, purpose.md, AGENTS.md are skipped — no `type:` frontmatter match."""
    for name in ("README.md", "purpose.md", "AGENTS.md"):
        (tmp_path / name).write_text("# Not a page\n\nbody\n", encoding="utf-8")
    pages = list(walk_wiki(tmp_path))
    assert pages == []


def test_walk_raises_on_invalid_page(tmp_path: Path) -> None:
    """A file with a recognized `type:` but bad frontmatter must raise loudly."""
    (tmp_path / "knowledge").mkdir()
    (tmp_path / "knowledge" / "broken.md").write_text(
        "---\ntype: decision\nstatus: active\n---\nbody\n",
        encoding="utf-8",
    )
    with pytest.raises(Exception):  # noqa: B017,PT011 — pydantic.ValidationError
        list(walk_wiki(tmp_path))


def test_walk_root_must_be_directory(tmp_path: Path) -> None:
    not_a_dir = tmp_path / "missing"
    with pytest.raises(NotADirectoryError):
        list(walk_wiki(not_a_dir))


def test_apollo_decisions_are_active_and_contradicting() -> None:
    """Sanity check: the seeded contradiction is present and unmarked.

    NB 08's contradiction-detection demo depends on both Apollo decisions
    being status=active with empty `contradicts[]`. Anyone editing the
    fixtures should not "fix" them by adding a supersedes/contradicts —
    that'd defeat the demo.
    """
    pages = {p.wikilink: p for p in walk_wiki(POC_WIKI)}
    q2 = pages["knowledge/decisions/apollo-q2-ship"]
    q3 = pages["knowledge/decisions/apollo-q3-postpone"]
    assert q2.page.status.value == "active"
    assert q3.page.status.value == "active"
    assert q2.page.contradicts == []
    assert q3.page.contradicts == []
    assert q2.page.supersedes is None
    assert q3.page.supersedes is None
