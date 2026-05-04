"""Generic wiki page iteration with frontmatter parsing.

`walk_wiki` yields one ``WikiPage`` per validated ``.md`` page under a
wiki root. Both lint (read-only contradiction/staleness scan) and the
archive backlink rewriter (read-then-rewrite) consume the same iterator
— keeping the walker as one primitive avoids two slightly-different
"walk every page" loops.

Skip rules (design §5.2.1, CLAUDE.md notes):

- Hidden directories (`.git`, `.obsidian`, …).
- ``raw/`` at the wiki root — the inbox, not part of the wiki.
- ``sources/raw/`` — durable archive of originals, also not pages.
- Any ``.md`` whose frontmatter ``type`` doesn't match a ``PageType``
  (covers ``purpose.md``, ``AGENTS.md``, ``README.md`` without listing
  them by name).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import frontmatter
from pydantic import TypeAdapter

from engine.models.pages import Page, PageType

_PAGE_ADAPTER = TypeAdapter(Page)

_SKIP_SUBTREES: frozenset[tuple[str, ...]] = frozenset(
    {
        ("raw",),
        ("sources", "raw"),
    }
)


@dataclass(frozen=True)
class WikiPage:
    """One validated page on disk.

    ``path`` is absolute (for read/write). ``wikilink`` is the
    extension-less, root-relative form used in ``[[wikilink]]`` body
    references and in ``related[]`` frontmatter (e.g.
    ``"knowledge/decisions/apollo-q2-ship"``).
    """

    path: Path
    wikilink: str
    page_type: PageType
    page: Page
    body: str


def _is_skipped(rel_parts: tuple[str, ...]) -> bool:
    """True if any leading slice of ``rel_parts`` matches a skip rule."""
    if any(part.startswith(".") for part in rel_parts):
        return True
    return any(rel_parts[: len(skip)] == skip for skip in _SKIP_SUBTREES)


def walk_wiki(root: Path) -> Iterator[WikiPage]:
    """Yield every validated wiki page under ``root``.

    Files that lack a recognized ``type`` in frontmatter are silently
    skipped (they're config, README, or otherwise not pages). Files
    that *do* declare a page type but fail Pydantic validation raise
    ``pydantic.ValidationError`` immediately — silent skipping there
    would mask the kind of breakage lint exists to catch.
    """
    if not root.is_dir():
        raise NotADirectoryError(f"wiki root is not a directory: {root}")

    for md_path in sorted(root.rglob("*.md")):
        if not md_path.is_file():
            continue
        rel = md_path.relative_to(root)
        if _is_skipped(rel.parts):
            continue

        post = frontmatter.load(md_path)
        page_type_str = post.metadata.get("type")
        try:
            page_type = PageType(page_type_str)
        except (ValueError, TypeError):
            continue

        page = _PAGE_ADAPTER.validate_python(post.metadata)
        wikilink = str(rel.with_suffix("")).replace("\\", "/")

        yield WikiPage(
            path=md_path,
            wikilink=wikilink,
            page_type=page_type,
            page=page,
            body=post.content,
        )


__all__ = ["WikiPage", "walk_wiki"]
