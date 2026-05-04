"""Admin operations for the L1/L2 caches: stats, gc, clear.

L3 (Anthropic prompt cache) has no on-disk surface — its TTL is
managed server-side. The CLI commands ``marginalia cache *`` are thin
wrappers over these functions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

ANALYSIS_DIR = "analysis"
LLM_DIR = "llm"


@dataclass(frozen=True)
class LayerStats:
    """One layer's on-disk footprint."""

    layer: str
    entries: int
    bytes: int
    oldest: datetime | None
    newest: datetime | None


@dataclass(frozen=True)
class CacheStats:
    """Stats for an entire cache root (both L1 and L2)."""

    root: Path
    layers: list[LayerStats]

    @property
    def total_entries(self) -> int:
        return sum(layer.entries for layer in self.layers)

    @property
    def total_bytes(self) -> int:
        return sum(layer.bytes for layer in self.layers)


def _read_cached_at(path: Path) -> datetime | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return datetime.fromisoformat(str(payload["cached_at"]))
    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
        return None


def _layer_stats(layer_root: Path, label: str) -> LayerStats:
    if not layer_root.is_dir():
        return LayerStats(layer=label, entries=0, bytes=0, oldest=None, newest=None)

    entries = 0
    total_bytes = 0
    oldest: datetime | None = None
    newest: datetime | None = None
    for p in layer_root.glob("*.json"):
        entries += 1
        total_bytes += p.stat().st_size
        ts = _read_cached_at(p)
        if ts is None:
            continue
        if oldest is None or ts < oldest:
            oldest = ts
        if newest is None or ts > newest:
            newest = ts
    return LayerStats(
        layer=label,
        entries=entries,
        bytes=total_bytes,
        oldest=oldest,
        newest=newest,
    )


def stats(cache_root: Path) -> CacheStats:
    """Inventory the on-disk cache.

    Reports per-layer entry counts, byte totals, and timestamps. Used
    by ``marginalia cache stats`` and the notebook receipts.
    """
    root = Path(cache_root)
    return CacheStats(
        root=root,
        layers=[
            _layer_stats(root / ANALYSIS_DIR, "L1 (analysis)"),
            _layer_stats(root / LLM_DIR, "L2 (llm)"),
        ],
    )


def gc(cache_root: Path, *, age_days: float) -> int:
    """Delete entries older than ``age_days``. Returns count removed.

    ``age_days`` may be a float; ``0`` means "everything older than
    right now" — exercise the path without timing acrobatics.
    """
    root = Path(cache_root)
    cutoff = datetime.now(UTC) - timedelta(days=age_days)
    removed = 0
    for layer_dir in (root / ANALYSIS_DIR, root / LLM_DIR):
        if not layer_dir.is_dir():
            continue
        for p in layer_dir.glob("*.json"):
            ts = _read_cached_at(p)
            if ts is None or ts <= cutoff:
                p.unlink(missing_ok=True)
                removed += 1
    return removed


def clear_all(cache_root: Path) -> int:
    """Delete every L1 and L2 entry. Returns count removed.

    L3 (Anthropic) is server-side and not affected.
    """
    root = Path(cache_root)
    removed = 0
    for layer_dir in (root / ANALYSIS_DIR, root / LLM_DIR):
        if not layer_dir.is_dir():
            continue
        for p in layer_dir.glob("*.json"):
            p.unlink(missing_ok=True)
            removed += 1
    return removed


__all__ = [
    "ANALYSIS_DIR",
    "LLM_DIR",
    "CacheStats",
    "LayerStats",
    "clear_all",
    "gc",
    "stats",
]
