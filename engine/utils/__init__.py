"""Shared utility helpers for the engine.

Only leaf modules (no agent or adapter imports) are re-exported
eagerly. Higher-level harnesses live alongside but must be imported
by full path to avoid circular imports through `api_compat`:

    from engine.utils.model_comparison import run_one
    from engine.utils.dispatch import extract, UnsupportedExtensionError
"""

from engine.utils.api_compat import temperature_kwargs
from engine.utils.cost_tracker import (
    PRICES,
    CostRecord,
    UnknownModelError,
    estimate_cost_usd,
    record_attempt,
)

__all__ = [
    "PRICES",
    "CostRecord",
    "UnknownModelError",
    "estimate_cost_usd",
    "record_attempt",
    "temperature_kwargs",
]
