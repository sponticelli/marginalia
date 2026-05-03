"""``marginalia.upsert_page`` — write a validated page to the wiki (design §7.4)."""

from __future__ import annotations

from pathlib import Path

import frontmatter
from pydantic import TypeAdapter

from engine.models.pages import Page

_PAGE_ADAPTER = TypeAdapter(Page)


def upsert_page(
    path: str,
    frontmatter_dict: dict,
    body: str,
    *,
    wiki_root: Path,
) -> Path:
    """Validate frontmatter, then write ``wiki_root/<path>.md``.

    ``path`` is the wikilink-style target without extension; the
    function appends ``.md`` and creates parent directories. Returns
    the absolute file path written.

    Frontmatter is validated through the discriminated ``Page`` union
    *before* the file is touched — invalid input never partially
    writes. Existing files are overwritten.
    """
    _PAGE_ADAPTER.validate_python(frontmatter_dict)
    target = wiki_root / f"{path}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    post = frontmatter.Post(content=body, **frontmatter_dict)
    target.write_text(frontmatter.dumps(post), encoding="utf-8")
    return target


__all__ = ["upsert_page"]
