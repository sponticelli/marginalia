"""Anthropic Messages API compatibility shims for model-specific quirks.

`temperature_kwargs` is the canonical place to add per-model overrides
of the `messages.create()` call shape. Today: Opus 4.7 rejects the
`temperature` parameter (`invalid_request_error: temperature is
deprecated for this model`), while Haiku 4.5 and Sonnet 4.6 still
accept `temperature=0` — which the engine relies on for L1/L2 cache
determinism (design §7.6).

Verified directly against the API on 2026-05-03:

- `claude-haiku-4-5`: temperature=0 → OK
- `claude-sonnet-4-6`: temperature=0 → OK
- `claude-opus-4-7`: temperature=0 → 400 invalid_request_error
"""

from __future__ import annotations


def temperature_kwargs(model: str) -> dict[str, float]:
    """Return ``{"temperature": 0}`` for models that accept it, ``{}`` otherwise.

    Determinism is the default — models that *can* take ``temperature=0``
    do, so analyze/synth runs over the same content reproduce. Opus 4.7
    is the lone exception today; add new prefixes here if/when the API
    deprecates the parameter on more models.
    """
    if model.startswith("claude-opus-4-7"):
        return {}
    return {"temperature": 0}


__all__ = ["temperature_kwargs"]
