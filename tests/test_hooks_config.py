"""Hook config loading: TOML parse, missing-file path, validation."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from engine.hooks.config import HookConfig, load_hook_config


def test_load_returns_none_when_missing(tmp_path: Path) -> None:
    assert load_hook_config(tmp_path / "config.toml") is None


def test_load_parses_both_events(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        "[hooks.on_ingest_complete]\n"
        'command = "/bin/echo"\n'
        "blocking = false\n"
        "timeout_s = 10\n"
        "\n"
        "[hooks.on_lint_complete]\n"
        'command = "/usr/bin/true"\n'
        "blocking = true\n"
        "timeout_s = 5\n",
        encoding="utf-8",
    )
    cfg = load_hook_config(cfg_path)
    assert isinstance(cfg, HookConfig)
    assert cfg.on_ingest_complete is not None
    assert cfg.on_ingest_complete.command == "/bin/echo"
    assert cfg.on_ingest_complete.blocking is False
    assert cfg.on_lint_complete is not None
    assert cfg.on_lint_complete.blocking is True
    assert cfg.on_lint_complete.timeout_s == 5


def test_load_partial_config_only_one_event(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        "[hooks.on_ingest_complete]\n" 'command = "echo"\n',
        encoding="utf-8",
    )
    cfg = load_hook_config(cfg_path)
    assert cfg is not None
    assert cfg.on_ingest_complete is not None
    assert cfg.on_lint_complete is None


def test_load_unknown_event_name_raises(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text('[hooks.on_made_up_event]\ncommand = "x"\n', encoding="utf-8")
    with pytest.raises(ValidationError):
        load_hook_config(cfg_path)


def test_for_event_returns_registered_hook(tmp_path: Path) -> None:
    cfg = HookConfig.model_validate(
        {"on_ingest_complete": {"command": "echo", "blocking": False, "timeout_s": 10}}
    )
    assert cfg.for_event("on_ingest_complete") is not None
    assert cfg.for_event("on_lint_complete") is None
