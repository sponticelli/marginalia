"""``marginalia.find_related`` — BFS the wiki wikilink graph (design §7.4)."""

from __future__ import annotations

from collections import deque
from pathlib import Path

from engine.agents.synthesis.cross_source import extract_wikilinks
from engine.models.pages import Page
from engine.tools.read_page import PageNotFoundError, read_page


def _outgoing_paths(page: Page) -> list[str]:
    """Inner paths of every wikilink in relational frontmatter fields."""
    out: list[str] = []
    for field in ("related", "contradicts"):
        for value in getattr(page, field, None) or []:
            if isinstance(value, str):
                out.extend(extract_wikilinks(value))
    supersedes = getattr(page, "supersedes", None)
    if isinstance(supersedes, str):
        out.extend(extract_wikilinks(supersedes))
    return out


def find_related(
    entity: str,
    *,
    wiki_root: Path,
    depth: int = 1,
) -> list[Page]:
    """Walk the wikilink graph starting at ``entity`` to ``depth`` hops.

    ``entity`` is a wiki path without extension (e.g. ``"sources/apollo-q2"``).
    Returns the discovered ``Page`` objects in BFS order, starting with
    the seed. Cycles are detected by visited-set; missing pages along
    the way are silently skipped (the seed itself raises
    ``PageNotFoundError`` if absent).
    """
    if depth < 0:
        raise ValueError("depth must be >= 0")

    seed_page, _ = read_page(entity, wiki_root=wiki_root)
    visited: set[str] = {entity}
    results: list[Page] = [seed_page]
    queue: deque[tuple[str, Page, int]] = deque([(entity, seed_page, 0)])

    while queue:
        current_path, current_page, current_depth = queue.popleft()
        if current_depth >= depth:
            continue
        for next_path in _outgoing_paths(current_page):
            if next_path in visited:
                continue
            visited.add(next_path)
            try:
                next_page, _ = read_page(next_path, wiki_root=wiki_root)
            except (PageNotFoundError, Exception):
                continue
            results.append(next_page)
            queue.append((next_path, next_page, current_depth + 1))

    return results


__all__ = ["find_related"]
