"""Hook config loaded from ``<wiki-root>/.wiki/config.toml`` (design §14.2).

The TOML shape:

    [hooks.on_ingest_complete]
    command   = "~/.wiki/hooks/notify-slack.sh"
    blocking  = false
    timeout_s = 10

    [hooks.on_lint_complete]
    command   = "~/.wiki/hooks/refresh-dashboard.py"
    blocking  = true
    timeout_s = 60

Missing file → ``load_hook_config()`` returns ``None`` and no hooks
fire. Per-event lookup: ``cfg.on_ingest_complete`` / ``cfg.on_lint_complete``
each yield an ``Hook`` or ``None``.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_TIMEOUT_S = 30
HOOK_EVENTS = ("on_ingest_complete", "on_lint_complete")


class Hook(BaseModel):
    """One hook registration: command + execution semantics."""

    model_config = ConfigDict(extra="forbid")

    command: str = Field(min_length=1, description="Shell command or path to executable.")
    blocking: bool = Field(
        default=False,
        description=(
            "If true, non-zero exit (or timeout) raises and fails the calling job. "
            "If false, failures are logged to audit_events but don't block."
        ),
    )
    timeout_s: int = Field(
        default=DEFAULT_TIMEOUT_S,
        ge=1,
        description="Max wall seconds before the hook is killed.",
    )


class HookConfig(BaseModel):
    """All hooks for one wiki, keyed by event name."""

    model_config = ConfigDict(extra="forbid")

    on_ingest_complete: Hook | None = None
    on_lint_complete: Hook | None = None

    def for_event(self, event: str) -> Hook | None:
        """Return the registered hook for ``event``, or ``None``."""
        return getattr(self, event, None)


def load_hook_config(toml_path: Path) -> HookConfig | None:
    """Load a `HookConfig` from `<wiki>/.wiki/config.toml`. Missing file → None.

    Bad TOML or unknown keys raise — config errors should surface
    loudly, not silently disable hooks. Unknown event names under
    ``[hooks]`` raise via Pydantic's ``extra="forbid"``.
    """
    p = Path(toml_path)
    if not p.is_file():
        return None

    with p.open("rb") as f:
        raw = tomllib.load(f)

    hooks_section = raw.get("hooks", {})
    if not isinstance(hooks_section, dict):
        raise ValueError(f"{p}: [hooks] section must be a table")
    return HookConfig.model_validate(hooks_section)


__all__ = [
    "DEFAULT_TIMEOUT_S",
    "HOOK_EVENTS",
    "Hook",
    "HookConfig",
    "load_hook_config",
]
