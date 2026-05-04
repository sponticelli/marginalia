"""marginalia audit CLI: verbs, --json output, empty-DB path."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from engine.audit import AuditWriter
from engine.cli.main import app
from engine.utils.cost_tracker import record_attempt

runner = CliRunner()


def _seed(db: Path) -> None:
    with AuditWriter(db) as w:
        w.record_ingest(
            job_id="j",
            source_ref="raw/x.md",
            source_hash="a" * 64,
            page_paths=["sources/x"],
            tokens_in=10,
            tokens_out=5,
            cost_usd=0.0001,
            duration_ms=42,
        )
        w.record_cost(
            record_attempt(
                agent="ingest",
                model="claude-haiku-4-5",
                tokens_in=10,
                tokens_out=5,
                cached=False,
            )
        )
        w.record_event(event_type="contradiction_found", metadata={"page": "X"})


def test_history_empty_db(tmp_path: Path) -> None:
    db = tmp_path / "audit.db"
    result = runner.invoke(app, ["audit", "history", "--db", str(db)])
    assert result.exit_code == 0
    assert "(no ingest history)" in result.output


def test_history_renders_table(tmp_path: Path) -> None:
    db = tmp_path / "audit.db"
    _seed(db)
    result = runner.invoke(app, ["audit", "history", "--db", str(db)])
    assert result.exit_code == 0
    assert "raw/x.md" in result.output
    assert "sources/x" in result.output


def test_history_json_emits_one_row_per_line(tmp_path: Path) -> None:
    db = tmp_path / "audit.db"
    _seed(db)
    result = runner.invoke(app, ["audit", "history", "--db", str(db), "--json"])
    assert result.exit_code == 0
    lines = [ln for ln in result.output.splitlines() if ln.strip()]
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["source_ref"] == "raw/x.md"
    assert payload["page_paths"] == ["sources/x"]


def test_cost_summary_renders(tmp_path: Path) -> None:
    db = tmp_path / "audit.db"
    _seed(db)
    result = runner.invoke(app, ["audit", "cost", "--db", str(db), "--days", "30"])
    assert result.exit_code == 0
    assert "claude-haiku-4-5" in result.output


def test_events_filter_by_type(tmp_path: Path) -> None:
    db = tmp_path / "audit.db"
    _seed(db)
    result = runner.invoke(
        app, ["audit", "events", "--db", str(db), "--type", "contradiction_found"]
    )
    assert result.exit_code == 0
    assert "contradiction_found" in result.output

    none_result = runner.invoke(app, ["audit", "events", "--db", str(db), "--type", "nonexistent"])
    assert none_result.exit_code == 0
    assert "(no events" in none_result.output
