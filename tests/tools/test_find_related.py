"""``marginalia.find_related`` — BFS depth + cycle handling."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import frontmatter

from engine.tools.find_related import find_related


def _write_concept(
    wiki_root: Path,
    slug: str,
    title: str,
    related: list[str] | None = None,
    body: str = "",
) -> None:
    fm = {
        "title": title,
        "type": "concept",
        "status": "active",
        "confidence": "medium",
        "created": date(2026, 5, 3).isoformat(),
        "last_synced": date(2026, 5, 3).isoformat(),
    }
    if related:
        fm["related"] = related
    target = wiki_root / f"{slug}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(frontmatter.dumps(frontmatter.Post(content=body, **fm)), encoding="utf-8")


def test_find_related_depth_one(tmp_path: Path) -> None:
    _write_concept(tmp_path, "concepts/a", "A", related=["[[concepts/b]]"])
    _write_concept(tmp_path, "concepts/b", "B")

    out = find_related("concepts/a", wiki_root=tmp_path, depth=1)
    titles = [p.title for p in out]
    assert titles == ["A", "B"]


def test_find_related_depth_two(tmp_path: Path) -> None:
    _write_concept(tmp_path, "concepts/a", "A", related=["[[concepts/b]]"])
    _write_concept(tmp_path, "concepts/b", "B", related=["[[concepts/c]]"])
    _write_concept(tmp_path, "concepts/c", "C")

    one = find_related("concepts/a", wiki_root=tmp_path, depth=1)
    assert {p.title for p in one} == {"A", "B"}

    two = find_related("concepts/a", wiki_root=tmp_path, depth=2)
    assert {p.title for p in two} == {"A", "B", "C"}


def test_find_related_handles_cycles(tmp_path: Path) -> None:
    _write_concept(tmp_path, "concepts/a", "A", related=["[[concepts/b]]"])
    _write_concept(tmp_path, "concepts/b", "B", related=["[[concepts/a]]"])
    out = find_related("concepts/a", wiki_root=tmp_path, depth=5)
    # No infinite loop; each visited at most once.
    titles = [p.title for p in out]
    assert sorted(titles) == ["A", "B"]
    assert len(titles) == 2


def test_find_related_skips_dangling_links(tmp_path: Path) -> None:
    _write_concept(tmp_path, "concepts/a", "A", related=["[[concepts/missing]]"])
    out = find_related("concepts/a", wiki_root=tmp_path, depth=2)
    assert [p.title for p in out] == ["A"]
