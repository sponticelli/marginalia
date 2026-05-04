"""Activity log generator (Phase 4 §4.3).

The generator reads `ingest_history` rows and renders markdown.
Tests cover: empty DB, missing DB file, single row, multi-row
ordering, multi-page ingests, the limit knob, and the on-disk
``write_activity_log`` helper.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from engine.audit.activity_log import (
    LOG_FILENAME,
    format_activity_log,
    write_activity_log,
)
from engine.audit.writer import AuditWriter


@pytest.fixture
def audit_db(tmp_path: Path) -> Path:
    """An initialised but empty audit.db. Tests seed via AuditWriter."""
    return tmp_path / "audit.db"


def _seed(audit_db_path: Path, rows: list[dict]) -> None:
    """Insert test rows via the public writer (so we exercise the same path the worker uses)."""
    with AuditWriter(audit_db_path) as w:
        for row in rows:
            w.record_ingest(**row)


# ─── empty / missing ─────────────────────────────────────────────────


def test_format_handles_missing_db_file(tmp_path: Path) -> None:
    """No file at the given path → placeholder, never an exception."""
    out = format_activity_log(tmp_path / "does-not-exist.db")
    assert "# Activity Log" in out
    assert "not initialised" in out


def test_format_handles_empty_db(audit_db: Path) -> None:
    """DB exists but no rows → placeholder."""
    AuditWriter(audit_db).close()  # init DDL, write nothing
    out = format_activity_log(audit_db)
    assert "# Activity Log" in out
    assert "No ingests recorded" in out


# ─── content shape ───────────────────────────────────────────────────


def test_format_renders_single_row(audit_db: Path) -> None:
    ts = datetime(2026, 5, 4, 14, 22, 3, tzinfo=UTC)
    _seed(
        audit_db,
        [
            {
                "job_id": "j1",
                "source_ref": "https://example.com/a",
                "source_hash": "h1",
                "page_paths": ["knowledge/concepts/a.md"],
                "tokens_in": 100,
                "tokens_out": 50,
                "cost_usd": 0.0123,
                "duration_ms": 1400,
                "timestamp": ts,
            }
        ],
    )
    out = format_activity_log(audit_db)
    assert "https://example.com/a" in out
    assert "knowledge/concepts/a.md" in out
    assert "$0.0123" in out
    assert "1.4s" in out
    assert "2026-05-04T14:22:03" in out


def test_format_renders_multi_page_ingest(audit_db: Path) -> None:
    """An ingest that wrote multiple pages should render all of them."""
    _seed(
        audit_db,
        [
            {
                "job_id": "j1",
                "source_ref": "/tmp/notes.md",
                "source_hash": "h1",
                "page_paths": ["knowledge/a.md", "knowledge/b.md"],
                "tokens_in": 1,
                "tokens_out": 1,
                "cost_usd": 0.001,
                "duration_ms": 100,
            }
        ],
    )
    out = format_activity_log(audit_db)
    assert "`knowledge/a.md`" in out
    assert "`knowledge/b.md`" in out


def test_format_renders_zero_page_ingest_with_placeholder(audit_db: Path) -> None:
    """An ingest with page_paths=[] (failure / draft) should still render readably."""
    _seed(
        audit_db,
        [
            {
                "job_id": "j1",
                "source_ref": "/tmp/x.md",
                "source_hash": "h1",
                "page_paths": [],
                "tokens_in": 1,
                "tokens_out": 1,
                "cost_usd": 0.001,
                "duration_ms": 100,
            }
        ],
    )
    out = format_activity_log(audit_db)
    assert "no pages written" in out


# ─── ordering + limit ────────────────────────────────────────────────


def test_format_orders_newest_first(audit_db: Path) -> None:
    """Rows must come out in DESC timestamp order — `last_n_ingests`'s contract."""
    base = datetime(2026, 5, 1, tzinfo=UTC)
    _seed(
        audit_db,
        [
            {
                "job_id": f"j{i}",
                "source_ref": f"src-{i}",
                "source_hash": f"h{i}",
                "page_paths": [f"p{i}.md"],
                "tokens_in": 1,
                "tokens_out": 1,
                "cost_usd": 0.001,
                "duration_ms": 100,
                "timestamp": base + timedelta(hours=i),
            }
            for i in range(3)
        ],
    )
    out = format_activity_log(audit_db)
    # newest = i=2; oldest = i=0
    assert out.find("src-2") < out.find("src-1") < out.find("src-0")


def test_format_respects_limit(audit_db: Path) -> None:
    """`limit=N` caps the rendered rows even when more exist."""
    _seed(
        audit_db,
        [
            {
                "job_id": f"j{i}",
                "source_ref": f"src-{i}",
                "source_hash": f"h{i}",
                "page_paths": ["p.md"],
                "tokens_in": 1,
                "tokens_out": 1,
                "cost_usd": 0.001,
                "duration_ms": 100,
                "timestamp": datetime(2026, 5, 1, tzinfo=UTC) + timedelta(hours=i),
            }
            for i in range(5)
        ],
    )
    out = format_activity_log(audit_db, limit=2)
    # Only the 2 newest should be present.
    assert "src-4" in out
    assert "src-3" in out
    assert "src-2" not in out


# ─── write_activity_log ──────────────────────────────────────────────


def test_write_creates_log_md_in_wiki_root(tmp_path: Path) -> None:
    """`--write` materializes to <wiki>/log.md alongside the audit DB."""
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    audit = wiki / ".wiki" / "audit.db"
    audit.parent.mkdir()
    _seed(
        audit,
        [
            {
                "job_id": "j1",
                "source_ref": "/tmp/x.md",
                "source_hash": "h1",
                "page_paths": ["a.md"],
                "tokens_in": 1,
                "tokens_out": 1,
                "cost_usd": 0.001,
                "duration_ms": 100,
            }
        ],
    )
    out = write_activity_log(wiki)
    assert out == wiki / LOG_FILENAME
    assert out.is_file()
    assert "Activity Log" in out.read_text()


def test_write_is_idempotent(tmp_path: Path) -> None:
    """Two consecutive calls produce byte-identical output."""
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    audit = wiki / ".wiki" / "audit.db"
    audit.parent.mkdir()
    _seed(
        audit,
        [
            {
                "job_id": "j1",
                "source_ref": "/tmp/x.md",
                "source_hash": "h1",
                "page_paths": ["a.md"],
                "tokens_in": 1,
                "tokens_out": 1,
                "cost_usd": 0.001,
                "duration_ms": 100,
                "timestamp": datetime(2026, 5, 4, tzinfo=UTC),  # pin so output is deterministic
            }
        ],
    )
    write_activity_log(wiki)
    first = (wiki / LOG_FILENAME).read_bytes()
    write_activity_log(wiki)
    second = (wiki / LOG_FILENAME).read_bytes()
    assert first == second
