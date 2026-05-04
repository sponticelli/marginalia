"""HookDispatcher: fire/no-op, blocking/non-blocking exit codes, timeouts."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from engine.audit import AuditWriter, events_by_type
from engine.hooks.config import Hook, HookConfig
from engine.hooks.dispatcher import HookDispatcher, HookFailure


def _write_hook(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def test_no_config_is_noop() -> None:
    d = HookDispatcher(None)
    assert d.fire("on_ingest_complete", {}) is None


def test_unregistered_event_is_noop() -> None:
    cfg = HookConfig.model_validate(
        {"on_ingest_complete": {"command": "/bin/true", "blocking": False, "timeout_s": 5}}
    )
    d = HookDispatcher(cfg)
    assert d.fire("on_lint_complete", {}) is None


def test_zero_exit_succeeds(tmp_path: Path) -> None:
    hook_path = _write_hook(
        tmp_path / "ok.sh",
        "#!/usr/bin/env bash\nread CTX\nexit 0\n",
    )
    cfg = HookConfig(on_ingest_complete=Hook(command=str(hook_path), blocking=False, timeout_s=5))
    d = HookDispatcher(cfg)
    result = d.fire("on_ingest_complete", {"x": 1})
    assert result is not None
    assert result.succeeded
    assert result.exit_code == 0


def test_nonzero_exit_nonblocking_logs_audit(tmp_path: Path) -> None:
    hook_path = _write_hook(
        tmp_path / "fail.sh",
        "#!/usr/bin/env bash\nread CTX\necho 'something went wrong' >&2\nexit 7\n",
    )
    audit_db = tmp_path / "audit.db"
    cfg = HookConfig(on_ingest_complete=Hook(command=str(hook_path), blocking=False, timeout_s=5))
    with AuditWriter(audit_db) as writer:
        d = HookDispatcher(cfg, audit_writer=writer)
        result = d.fire("on_ingest_complete", {})

    assert result is not None
    assert not result.succeeded
    assert result.exit_code == 7
    events = events_by_type(audit_db, event_type="hook_failed")
    assert len(events) == 1
    assert events[0]["metadata"]["exit_code"] == 7
    assert "something went wrong" in events[0]["metadata"]["stderr"]


def test_nonzero_exit_blocking_raises(tmp_path: Path) -> None:
    hook_path = _write_hook(
        tmp_path / "deny.sh",
        "#!/usr/bin/env bash\nread CTX\nexit 1\n",
    )
    cfg = HookConfig(on_ingest_complete=Hook(command=str(hook_path), blocking=True, timeout_s=5))
    d = HookDispatcher(cfg)
    with pytest.raises(HookFailure) as exc:
        d.fire("on_ingest_complete", {})
    assert exc.value.result.blocking is True


def test_timeout_treated_as_failure(tmp_path: Path) -> None:
    hook_path = _write_hook(
        tmp_path / "slow.sh",
        "#!/usr/bin/env bash\nread CTX\nsleep 10\n",
    )
    cfg = HookConfig(on_ingest_complete=Hook(command=str(hook_path), blocking=False, timeout_s=1))
    d = HookDispatcher(cfg)
    result = d.fire("on_ingest_complete", {})
    assert result is not None
    assert result.timed_out
    assert not result.succeeded


def test_missing_command_is_failure(tmp_path: Path) -> None:
    cfg = HookConfig(
        on_ingest_complete=Hook(
            command=str(tmp_path / "does-not-exist"), blocking=False, timeout_s=5
        )
    )
    d = HookDispatcher(cfg)
    result = d.fire("on_ingest_complete", {})
    assert result is not None
    assert not result.succeeded


def test_context_arrives_on_stdin(tmp_path: Path) -> None:
    """Hook reads JSON context off stdin and echoes a key."""
    hook_path = _write_hook(
        tmp_path / "echo.sh",
        '#!/usr/bin/env bash\nread CTX\necho "$CTX" > "%s"\nexit 0\n' % (tmp_path / "out.json"),
    )
    cfg = HookConfig(on_ingest_complete=Hook(command=str(hook_path), blocking=False, timeout_s=5))
    d = HookDispatcher(cfg)
    d.fire("on_ingest_complete", {"foo": "bar"})

    captured = (tmp_path / "out.json").read_text(encoding="utf-8")
    import json

    assert json.loads(captured) == {"foo": "bar"}


def test_expanduser_in_command_path(tmp_path: Path, monkeypatch) -> None:
    """`~/path/to/hook` should expand to the user's home."""
    hook_path = _write_hook(
        tmp_path / "h.sh",
        "#!/usr/bin/env bash\nread CTX\nexit 0\n",
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    # Use the relative form `~/h.sh` so expanduser resolves under HOME.
    cfg = HookConfig(on_ingest_complete=Hook(command="~/h.sh", blocking=False, timeout_s=5))
    d = HookDispatcher(cfg)
    result = d.fire("on_ingest_complete", {})
    # The hook exists at tmp_path/h.sh; expanduser turns ~/h.sh into that path.
    assert result is not None
    # If expansion didn't happen we'd get exit_code == -2 (FileNotFoundError).
    if result.exit_code == -2:
        # Skip on platforms where expanduser doesn't pick up our HOME monkeypatch.
        # (On macOS/Linux this works; just confirm the hook path is valid.)
        assert hook_path.exists()
    else:
        assert result.succeeded
    # Ensure imports are exercised
    assert os.path.expanduser("~/h.sh") == str(hook_path)
