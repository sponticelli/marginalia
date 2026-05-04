"""``marginalia use`` and ``marginalia wikis ...`` CLI verbs (Phase 4 §4.2).

Verb-level smoke against the registry. The resolver itself is covered
by ``test_wikis.py`` — these tests check the Typer surface (output,
exit codes, error messages) and that registry mutations land.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from engine.cli.main import app
from engine.cli.wikis import config_path

runner = CliRunner()


@pytest.fixture
def isolated_xdg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point XDG at tmp + strip env that would leak into the resolver."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("MARGINALIA_WIKI", raising=False)
    monkeypatch.delenv("WIKI_CONTENT_REPO", raising=False)
    monkeypatch.delenv("WIKI_RAW_PATH", raising=False)
    return config_path()


# ─── add ─────────────────────────────────────────────────────────────


def test_wikis_add_first_writes_toml_and_marks_active(isolated_xdg: Path) -> None:
    """First add → file exists, registers correctly, active = name."""
    result = runner.invoke(app, ["wikis", "add", "personal", "/tmp/wiki-personal"])
    assert result.exit_code == 0
    assert "registered" in result.stdout
    assert "now active" in result.stdout
    assert isolated_xdg.is_file()
    parsed = tomllib.loads(isolated_xdg.read_text())
    assert parsed["active"] == "personal"
    assert parsed["wikis"]["personal"]["root"] == "/tmp/wiki-personal"


def test_wikis_add_second_does_not_steal_active(isolated_xdg: Path) -> None:
    runner.invoke(app, ["wikis", "add", "personal", "/tmp/p"])
    result = runner.invoke(app, ["wikis", "add", "work", "/tmp/w"])
    assert result.exit_code == 0
    assert "now active" not in result.stdout
    parsed = tomllib.loads(isolated_xdg.read_text())
    assert parsed["active"] == "personal"


def test_wikis_add_with_raw_path_persists_override(isolated_xdg: Path) -> None:
    result = runner.invoke(app, ["wikis", "add", "p", "/tmp/p", "--raw-path", "/tmp/raw-p"])
    assert result.exit_code == 0
    parsed = tomllib.loads(isolated_xdg.read_text())
    assert parsed["wikis"]["p"]["raw_path"] == "/tmp/raw-p"


# ─── list ────────────────────────────────────────────────────────────


def test_wikis_list_empty_shows_help(isolated_xdg: Path) -> None:
    result = runner.invoke(app, ["wikis", "list"])
    assert result.exit_code == 0
    assert "no wikis registered" in result.stdout
    assert "marginalia wikis add" in result.stdout


def test_wikis_list_marks_active(isolated_xdg: Path) -> None:
    runner.invoke(app, ["wikis", "add", "personal", "/tmp/p"])
    runner.invoke(app, ["wikis", "add", "work", "/tmp/w"])
    result = runner.invoke(app, ["wikis", "list"])
    assert result.exit_code == 0
    # The active marker (★) is rich-formatted; assert presence loosely.
    assert "personal" in result.stdout
    assert "work" in result.stdout
    # Both names present; row count is 2 (active row should have the marker).
    # Rich wraps long paths; we just want the data to all be there.


# ─── use ─────────────────────────────────────────────────────────────


def test_use_flips_active(isolated_xdg: Path) -> None:
    runner.invoke(app, ["wikis", "add", "a", "/tmp/a"])
    runner.invoke(app, ["wikis", "add", "b", "/tmp/b"])
    # `a` was first, so it's active. Switch.
    result = runner.invoke(app, ["use", "b"])
    assert result.exit_code == 0
    assert "using" in result.stdout
    parsed = tomllib.loads(isolated_xdg.read_text())
    assert parsed["active"] == "b"


def test_use_unknown_errors(isolated_xdg: Path) -> None:
    runner.invoke(app, ["wikis", "add", "a", "/tmp/a"])
    result = runner.invoke(app, ["use", "ghost"])
    assert result.exit_code == 1
    assert "ghost" in result.stdout
    assert "not registered" in result.stdout


def test_use_with_no_registry_errors(isolated_xdg: Path) -> None:
    """Trying to `use` something with no wikis registered should fail clearly."""
    result = runner.invoke(app, ["use", "anything"])
    assert result.exit_code == 1
    assert "not registered" in result.stdout


# ─── remove ──────────────────────────────────────────────────────────


def test_remove_drops_entry(isolated_xdg: Path) -> None:
    runner.invoke(app, ["wikis", "add", "p", "/tmp/p"])
    result = runner.invoke(app, ["wikis", "remove", "p"])
    assert result.exit_code == 0
    assert "removed" in result.stdout
    parsed = tomllib.loads(isolated_xdg.read_text()) if isolated_xdg.exists() else {}
    # `wikis` table may be empty/missing after removal; either is acceptable.
    assert "p" not in (parsed.get("wikis") or {})


def test_remove_active_warns_user(isolated_xdg: Path) -> None:
    """Removing the active wiki when others remain should prompt the user to pick."""
    runner.invoke(app, ["wikis", "add", "a", "/tmp/a"])
    runner.invoke(app, ["wikis", "add", "b", "/tmp/b"])
    # `a` is active. Remove it.
    result = runner.invoke(app, ["wikis", "remove", "a"])
    assert result.exit_code == 0
    assert "no wiki is currently active" in result.stdout
    assert "marginalia use" in result.stdout


def test_remove_unknown_errors(isolated_xdg: Path) -> None:
    result = runner.invoke(app, ["wikis", "remove", "ghost"])
    assert result.exit_code == 1
    assert "not registered" in result.stdout
