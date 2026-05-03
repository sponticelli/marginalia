"""``marginalia.upsert_page`` — write + overwrite + frontmatter validation."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import frontmatter
import pytest
from pydantic import ValidationError

from engine.tools.upsert_page import upsert_page


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


def test_upsert_page_writes_disk(tmp_path: Path) -> None:
    target = upsert_page(
        "sources/foo",
        _good_source_fm("Foo"),
        "# Foo body\n",
        wiki_root=tmp_path,
    )
    assert target.exists()
    post = frontmatter.load(target)
    assert post.metadata["title"] == "Foo"
    assert "Foo body" in post.content


def test_upsert_page_overwrites_existing(tmp_path: Path) -> None:
    upsert_page("sources/x", _good_source_fm("X1"), "first", wiki_root=tmp_path)
    upsert_page("sources/x", _good_source_fm("X2"), "second", wiki_root=tmp_path)
    post = frontmatter.load(tmp_path / "sources/x.md")
    assert post.metadata["title"] == "X2"
    assert post.content.strip() == "second"


def test_upsert_page_rejects_invalid_frontmatter(tmp_path: Path) -> None:
    bad = {"title": "x", "type": "source"}  # missing required fields
    with pytest.raises(ValidationError):
        upsert_page("sources/bad", bad, "body", wiki_root=tmp_path)
    # File should not have been created.
    assert not (tmp_path / "sources/bad.md").exists()
