"""L1 cache: file-backed store for ``SourceAnalysis`` outputs (design §7.6).

The L1 layer wraps the cacheable ingest analyze step. Each entry is a
JSON file under ``<root>/<key>.json`` whose body carries the analysis
plus a small envelope (``cached_at``, ``cache_version``) so we can GC
stale entries and detect cross-version data on read.

Atomic writes use ``os.replace`` after a tempfile dance — two workers
racing on the same key produce one valid file, never a half-written
one.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from engine.agents.ingest.analyze import SourceAnalysis
from engine.cache import CACHE_VERSION

ENVELOPE_KEY = "analysis"


@dataclass(frozen=True)
class L1Entry:
    """One on-disk L1 record: envelope + parsed analysis."""

    cached_at: datetime
    cache_version: str
    analysis: SourceAnalysis


class AnalysisCache:
    """L1 cache for ``analyze_source`` outputs.

    Storage: ``<root>/<key>.json``. The store is dumb — callers build
    the key via ``engine.cache.keys.build_l1_key`` and pass it in.
    """

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def get(self, key: str) -> SourceAnalysis | None:
        """Return the cached ``SourceAnalysis`` or ``None`` on miss.

        Returns ``None`` (not raise) on parse failure — a corrupt
        entry should be treated as a miss so the next write replaces
        it cleanly.
        """
        path = self.path_for(key)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return SourceAnalysis.model_validate(payload[ENVELOPE_KEY])
        except (json.JSONDecodeError, KeyError, ValueError):
            return None

    def set(self, key: str, analysis: SourceAnalysis) -> None:
        """Atomically write an entry. Concurrent writers produce one valid file."""
        envelope = {
            "cached_at": datetime.now(UTC).isoformat(),
            "cache_version": CACHE_VERSION,
            ENVELOPE_KEY: analysis.model_dump(mode="json"),
        }
        self._atomic_write_json(self.path_for(key), envelope)

    def get_entry(self, key: str) -> L1Entry | None:
        """Return the full envelope (for admin/stats; ``get`` is the hot path)."""
        path = self.path_for(key)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return L1Entry(
                cached_at=datetime.fromisoformat(payload["cached_at"]),
                cache_version=str(payload["cache_version"]),
                analysis=SourceAnalysis.model_validate(payload[ENVELOPE_KEY]),
            )
        except (json.JSONDecodeError, KeyError, ValueError):
            return None

    def clear(self) -> int:
        """Delete every entry. Returns count removed."""
        n = 0
        for p in self.root.glob("*.json"):
            p.unlink(missing_ok=True)
            n += 1
        return n

    @staticmethod
    def _atomic_write_json(target: Path, payload: dict) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=target.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, target)
        except Exception:
            Path(tmp).unlink(missing_ok=True)
            raise


__all__ = ["AnalysisCache", "L1Entry"]
