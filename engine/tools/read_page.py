"""``marginalia.read_page`` — fetch a wiki page by path (design §7.4)."""

from __future__ import annotations

from pathlib import Path

import frontmatter
from pydantic import TypeAdapter

from engine.models.pages import Page

_PAGE_ADAPTER = TypeAdapter(Page)


class PageNotFoundError(FileNotFoundError):
    """Raised when ``read_page`` is asked for a path that doesn't exist."""


def read_page(path: str, *, wiki_root: Path) -> tuple[Page, str]:
    """Read and validate a wiki page; return ``(page, body)``.

    ``path`` is the wikilink-style path *without* the ``.md`` suffix
    (e.g. ``"sources/apollo-launch-blockers-sync"``).

    Frontmatter validates against the discriminated ``Page`` union;
    malformed frontmatter raises ``pydantic.ValidationError``.
    """
    md_path = wiki_root / f"{path}.md"
    if not md_path.exists():
        raise PageNotFoundError(f"no page at {md_path}")
    post = frontmatter.load(md_path)
    page = _PAGE_ADAPTER.validate_python(post.metadata)
    return page, post.content


__all__ = ["PageNotFoundError", "read_page"]
