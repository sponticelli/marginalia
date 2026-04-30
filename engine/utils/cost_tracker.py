"""Per-attempt cost tracking (design §13.2 `cost_records` table).

Prices are USD per 1M tokens, current as of 2026-04-30. Re-verify
before relying on totals — Anthropic publishes pricing changes
periodically and this table is the authoritative number locally.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

PRICES: dict[str, dict[str, float]] = {
    "claude-haiku-4-5": {"input_per_million": 1.00, "output_per_million": 5.00},
    "claude-sonnet-4-6": {"input_per_million": 3.00, "output_per_million": 15.00},
    "claude-opus-4-7": {"input_per_million": 15.00, "output_per_million": 75.00},
}


class UnknownModelError(KeyError):
    """Raised when a model id is not present in the `PRICES` table."""


class CostRecord(BaseModel):
    """One attempt's cost, shaped for the §13.2 `cost_records` table.

    Field names mirror the SQLite columns so a row can be written via
    `record.model_dump()` once the audit DB is wired up.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    job_id: str | None = None
    agent: str = Field(description="ingest | synthesis | qa | lint | scaffold")
    model: str
    tokens_in: int = Field(ge=0)
    tokens_out: int = Field(ge=0)
    cost_usd: float = Field(ge=0.0)
    cached: bool = False
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


def estimate_cost_usd(model: str, tokens_in: int, tokens_out: int) -> float:
    """Return USD cost for a single call, looked up from the `PRICES` table."""
    try:
        rates = PRICES[model]
    except KeyError as exc:
        raise UnknownModelError(
            f"no price entry for model {model!r}; update engine.utils.cost_tracker.PRICES"
        ) from exc
    return (tokens_in / 1_000_000) * rates["input_per_million"] + (tokens_out / 1_000_000) * rates[
        "output_per_million"
    ]


def record_attempt(
    *,
    agent: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
    cached: bool = False,
    job_id: str | None = None,
) -> CostRecord:
    """Build a `CostRecord` for one model invocation.

    Cost is computed from the `PRICES` table — pass `cached=True` for
    L1/L2/L3 hits if you still want the row written; the cost will
    reflect the (likely-zero) tokens reported by the call.
    """
    cost = estimate_cost_usd(model, tokens_in, tokens_out)
    return CostRecord(
        agent=agent,
        model=model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost,
        cached=cached,
        job_id=job_id,
    )


__all__ = [
    "PRICES",
    "CostRecord",
    "UnknownModelError",
    "estimate_cost_usd",
    "record_attempt",
]
