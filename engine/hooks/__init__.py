"""Lifecycle hooks for the Marginalia engine (design §14).

Hooks are shell commands declared in ``<wiki>/.wiki/config.toml`` and
fired by the job dispatcher on events like ``on_ingest_complete`` and
``on_lint_complete``. Blocking hooks gate the job; non-blocking hooks
log failures to ``audit_events`` and let the job continue.
"""

from __future__ import annotations

from engine.hooks.config import (
    DEFAULT_TIMEOUT_S,
    HOOK_EVENTS,
    Hook,
    HookConfig,
    load_hook_config,
)
from engine.hooks.dispatcher import HookDispatcher, HookFailure, HookResult

__all__ = [
    "DEFAULT_TIMEOUT_S",
    "HOOK_EVENTS",
    "Hook",
    "HookConfig",
    "HookDispatcher",
    "HookFailure",
    "HookResult",
    "load_hook_config",
]
