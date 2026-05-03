"""``marginalia.search`` — term-overlap ranking, type filter, edge cases."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import frontmatter

from engine.models.pages import PageType
from engine.tools.search import search


def _write_source(
    wiki_root: Path,
    slug: str,
    title: str,
    body: str,
    *,
    tags: list[str] | None = None,
) -> None:
    fm = {
        "title": title,
        "type": "source",
        "status": "active",
        "created": date(2026, 5, 3).isoformat(),
        "last_synced": date(2026, 5, 3).isoformat(),
        "sources": [
            {
                "ref": slug,
                "kind": "local_file",
                "captured": date(2026, 5, 3).isoformat(),
                "authority": "canonical",
            }
        ],
        "tags": tags or [],
    }
    target = wiki_root / "sources" / f"{slug}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(frontmatter.dumps(frontmatter.Post(content=body, **fm)), encoding="utf-8")


def _write_concept(wiki_root: Path, slug: str, title: str, body: str) -> None:
    fm = {
        "title": title,
        "type": "concept",
        "status": "active",
        "confidence": "medium",
        "created": date(2026, 5, 3).isoformat(),
        "last_synced": date(2026, 5, 3).isoformat(),
    }
    target = wiki_root / "concepts" / f"{slug}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(frontmatter.dumps(frontmatter.Post(content=body, **fm)), encoding="utf-8")


def test_search_returns_score_ordered_hits(tmp_path: Path) -> None:
    _write_source(tmp_path, "apollo", "Apollo Launch", "Apollo project status update.")
    _write_source(tmp_path, "kubernetes", "Kubernetes Notes", "Kubernetes deployment notes.")
    _write_source(
        tmp_path,
        "apollo-meeting",
        "Apollo Meeting",
        "Apollo Apollo Apollo meeting notes about Apollo.",
    )

    hits = search("Apollo", wiki_root=tmp_path)
    assert len(hits) >= 2
    scores = [h.score for h in hits]
    assert scores == sorted(scores, reverse=True)
    assert hits[0].path == "sources/apollo-meeting"  # densest matches


def test_search_respects_limit(tmp_path: Path) -> None:
    for i in range(5):
        _write_source(tmp_path, f"apollo-{i}", f"Apollo {i}", "Apollo Apollo content.")
    hits = search("Apollo", wiki_root=tmp_path, limit=2)
    assert len(hits) == 2


def test_search_filters_by_type(tmp_path: Path) -> None:
    _write_source(tmp_path, "apollo-source", "Apollo Source", "Apollo Apollo content.")
    _write_concept(tmp_path, "apollo-concept", "Apollo Concept", "Apollo Apollo synthesis.")

    hits = search("Apollo", wiki_root=tmp_path, type=PageType.SOURCE)
    assert all(h.type == PageType.SOURCE for h in hits)
    assert any(h.path == "sources/apollo-source" for h in hits)
    assert not any(h.path == "concepts/apollo-concept" for h in hits)


def test_search_handles_empty_query(tmp_path: Path) -> None:
    _write_source(tmp_path, "x", "X", "content")
    assert search("", wiki_root=tmp_path) == []
    assert search("   ", wiki_root=tmp_path) == []


def test_search_skips_non_markdown(tmp_path: Path) -> None:
    _write_source(tmp_path, "apollo", "Apollo", "Apollo content.")
    (tmp_path / "stray.txt").write_text("Apollo Apollo Apollo", encoding="utf-8")
    (tmp_path / "stray.json").write_text('{"text": "Apollo"}', encoding="utf-8")

    hits = search("Apollo", wiki_root=tmp_path)
    paths = {h.path for h in hits}
    assert paths == {"sources/apollo"}  # JSON/TXT not indexed
