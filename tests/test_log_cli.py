"""``marginalia log`` CLI verb (Phase 4 §4.3).

Covers two scenarios that matter at the verb layer:

1. Defaults: stdout shows recent activity for the active wiki.
2. ``--write``: a `log.md` lands in the wiki root.

The audit-DB rendering is exhaustively tested in `test_activity_log.py`
— here we just check the CLI plumbing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from engine.audit.writer import AuditWriter
from engine.cli.main import app
from engine.cli.wikis import add_wiki, config_path

runner = CliRunner()


@pytest.fixture
def wiki_with_audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set up a wiki root + initialised audit.db, registered as the active wiki."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("MARGINALIA_WIKI", raising=False)
    monkeypatch.delenv("WIKI_CONTENT_REPO", raising=False)

    wiki = tmp_path / "wiki"
    wiki.mkdir()
    audit = wiki / ".wiki" / "audit.db"
    audit.parent.mkdir()
    with AuditWriter(audit) as w:
        w.record_ingest(
            job_id="j1",
            source_ref="https://example.com/article",
            source_hash="h1",
            page_paths=["knowledge/concepts/example.md"],
            tokens_in=100,
            tokens_out=50,
            cost_usd=0.0123,
            duration_ms=1400,
            timestamp=datetime(2026, 5, 4, 14, 22, 3, tzinfo=UTC),
        )
    add_wiki("test", wiki, path=config_path())
    return wiki


def test_log_renders_to_stdout(wiki_with_audit: Path) -> None:
    """No flags → human-readable activity in stdout."""
    result = runner.invoke(app, ["log"])
    assert result.exit_code == 0
    assert "Activity Log" in result.stdout
    assert "https://example.com/article" in result.stdout
    assert "knowledge/concepts/example.md" in result.stdout


def test_log_with_write_creates_log_md(wiki_with_audit: Path) -> None:
    """``--write`` produces a `log.md` next to the wiki root."""
    result = runner.invoke(app, ["log", "--write"])
    assert result.exit_code == 0
    log_md = wiki_with_audit / "log.md"
    assert log_md.is_file()
    assert "https://example.com/article" in log_md.read_text()


def test_log_respects_n_flag(wiki_with_audit: Path) -> None:
    """`-n 1` clamps the rendered count to 1."""
    # Add a second row.
    with AuditWriter(wiki_with_audit / ".wiki" / "audit.db") as w:
        w.record_ingest(
            job_id="j2",
            source_ref="https://example.com/older",
            source_hash="h2",
            page_paths=["knowledge/concepts/old.md"],
            tokens_in=1,
            tokens_out=1,
            cost_usd=0.001,
            duration_ms=100,
            timestamp=datetime(2026, 5, 4, 13, 0, 0, tzinfo=UTC),
        )
    result = runner.invoke(app, ["log", "-n", "1"])
    assert result.exit_code == 0
    # Newest is example.com/article (later timestamp); -n 1 should keep only that.
    assert "https://example.com/article" in result.stdout
    assert "https://example.com/older" not in result.stdout


def test_log_handles_no_audit_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fresh wiki with no audit.db → graceful placeholder, exit 0."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("MARGINALIA_WIKI", raising=False)
    monkeypatch.delenv("WIKI_CONTENT_REPO", raising=False)

    fresh = tmp_path / "fresh-wiki"
    fresh.mkdir()
    add_wiki("fresh", fresh, path=config_path())

    result = runner.invoke(app, ["log"])
    assert result.exit_code == 0
    assert "Activity Log" in result.stdout
    assert "not initialised" in result.stdout
