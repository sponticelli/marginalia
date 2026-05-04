"""``marginalia hooks test`` — fires a hook with synthetic context.

Two scenarios:

1. Happy path: a registered hook fires, captures stdout, exits 0.
2. Missing event registration: clear error + non-zero exit code.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from typer.testing import CliRunner

from engine.cli.main import app

runner = CliRunner()


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _write_config(path: Path, hook_command: str, blocking: bool = False) -> None:
    """Write a minimal config.toml registering ``hook_command`` for ingest."""
    blocking_lit = "true" if blocking else "false"
    path.write_text(
        f'[hooks.on_ingest_complete]\ncommand = "{hook_command}"\nblocking = {blocking_lit}\n'
        "timeout_s = 5\n",
        encoding="utf-8",
    )


def test_hooks_test_runs_registered_hook(tmp_path: Path) -> None:
    """End-to-end: config → registered hook → stdout captured + exit 0."""
    captured = tmp_path / "captured.json"
    hook = tmp_path / "echo-context.sh"
    _write_executable(
        hook,
        f'#!/usr/bin/env bash\nread CTX\necho "$CTX" > {captured}\necho "ok"\nexit 0\n',
    )
    config_path = tmp_path / "config.toml"
    _write_config(config_path, str(hook))

    result = runner.invoke(
        app,
        [
            "hooks",
            "test",
            "--event",
            "on_ingest_complete",
            "--config",
            str(config_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "exit_code: 0" in result.output
    # The hook captured the synthetic context — verify the test job_id is in it.
    assert "test-job-0001" in captured.read_text()


def test_hooks_test_missing_event_exits_nonzero(tmp_path: Path) -> None:
    """An event the config doesn't register → non-zero exit + helpful message."""
    config_path = tmp_path / "config.toml"
    config_path.write_text("# empty\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "hooks",
            "test",
            "--event",
            "on_ingest_complete",
            "--config",
            str(config_path),
        ],
    )
    assert result.exit_code == 1
    assert "no hook registered" in result.output


def test_hooks_test_missing_config_exits_nonzero(tmp_path: Path) -> None:
    """No config file at the resolved path → non-zero exit."""
    nonexistent = tmp_path / "nope.toml"
    result = runner.invoke(
        app,
        [
            "hooks",
            "test",
            "--event",
            "on_lint_complete",
            "--config",
            str(nonexistent),
        ],
    )
    assert result.exit_code == 1
    assert "no hook config found" in result.output


def test_hooks_test_unknown_event(tmp_path: Path) -> None:
    """An unsupported event name — Typer surfaces the error from _synthetic_context."""
    config_path = tmp_path / "config.toml"
    hook = tmp_path / "noop.sh"
    _write_executable(hook, "#!/usr/bin/env bash\nexit 0\n")
    _write_config(config_path, str(hook))

    result = runner.invoke(
        app,
        [
            "hooks",
            "test",
            "--event",
            "on_nothing_real",
            "--config",
            str(config_path),
        ],
    )
    # With a hook registered for ingest, we'd hit the synthetic-context check
    # only if the event matched — `on_nothing_real` falls out at the
    # for_event lookup. Either way, non-zero exit is required.
    assert result.exit_code != 0
    _ = os  # keep import used regardless of platform
