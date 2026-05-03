"""``marginalia.read_page`` — round-trip + validation."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import frontmatter
import pytest
from pydantic import ValidationError

from engine.models.pages import PageType
from engine.tools.read_page import PageNotFoundError, read_page


def _write(wiki_root: Path, path: str, fm: dict, body: str) -> None:
    target = wiki_root / f"{path}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(frontmatter.dumps(frontmatter.Post(content=body, **fm)), encoding="utf-8")


def _good_source_fm(title: str = "X") -> dict:
    return {
        "title": title,
        "type": "source",
        "status": "active",
        "created": date(2026, 5, 3).isoformat(),
        "last_synced": date(2026, 5, 3).isoformat(),
        "sources": [
            {
                "ref": "x",
                "kind": "local_file",
                "captured": date(2026, 5, 3).isoformat(),
                "authority": "canonical",
            }
        ],
    }


def test_read_page_round_trips_source(tmp_path: Path) -> None:
    _write(tmp_path, "sources/foo", _good_source_fm("Foo"), "# Foo\n\nbody text\n")
    page, body = read_page("sources/foo", wiki_root=tmp_path)
    assert page.type == PageType.SOURCE
    assert page.title == "Foo"
    assert "body text" in body


def test_read_page_validates_frontmatter(tmp_path: Path) -> None:
    bad_fm = {"title": "x", "type": "source"}  # missing required dates + sources
    _write(tmp_path, "sources/bad", bad_fm, "x")
    with pytest.raises(ValidationError):
        read_page("sources/bad", wiki_root=tmp_path)


def test_read_page_returns_body_separately(tmp_path: Path) -> None:
    body = "## Heading\n\nfirst paragraph.\n\nsecond paragraph.\n"
    _write(tmp_path, "sources/foo", _good_source_fm(), body)
    _, returned_body = read_page("sources/foo", wiki_root=tmp_path)
    # Body shouldn't include the frontmatter delimiters or fields.
    assert "title:" not in returned_body
    assert "## Heading" in returned_body


def test_read_page_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(PageNotFoundError):
        read_page("sources/does-not-exist", wiki_root=tmp_path)
