"""Shared utility helpers for the engine.

Only leaf modules (no agent imports) are re-exported eagerly. The
`model_comparison` harness imports from `engine.agents.ingest`, which
in turn imports `engine.utils.api_compat` — eager re-export here
would close the cycle. Import it directly:

    from engine.utils.model_comparison import run_one
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
