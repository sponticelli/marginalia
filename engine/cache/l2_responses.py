"""L2 cache: file-backed store for deterministic LLM responses (design §7.6).

Where L1 caches at the *function* level (``analyze_source`` output),
L2 caches at the *call* level — the rendered prompt + model id is the
key, the raw response text is the value. Useful for any deterministic
LLM call that doesn't have the schema discipline L1 enforces (lint
contradiction prompts, fixed scaffold prompts, mock helpers).

Atomic writes mirror the L1 store; the on-disk envelope captures
enough metadata (model, tokens, timestamp) for ``cache stats`` and
the audit DB to reconstruct cost.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from engine.cache import CACHE_VERSION

ENVELOPE_KEY = "response"


@dataclass(frozen=True)
class CachedLLMResponse:
    """A frozen LLM response payload suitable for round-tripping through L2.

    ``text`` is the model's reply body; ``tokens_in`` / ``tokens_out``
    are recorded so a cache hit can still log a ``CostRecord`` with
    ``cached=True`` and the original token counts.
    """

    text: str
    model: str
    tokens_in: int
    tokens_out: int


@dataclass(frozen=True)
class L2Entry:
    """One on-disk L2 record: envelope + parsed response."""

    cached_at: datetime
    cache_version: str
    response: CachedLLMResponse


class ResponseCache:
    """L2 cache for raw LLM responses keyed on rendered prompt + model id."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def get(self, key: str) -> CachedLLMResponse | None:
        path = self.path_for(key)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            r = payload[ENVELOPE_KEY]
            return CachedLLMResponse(
                text=str(r["text"]),
                model=str(r["model"]),
                tokens_in=int(r["tokens_in"]),
                tokens_out=int(r["tokens_out"]),
            )
        except (json.JSONDecodeError, KeyError, ValueError, TypeError):
            return None

    def set(self, key: str, response: CachedLLMResponse) -> None:
        envelope = {
            "cached_at": datetime.now(UTC).isoformat(),
            "cache_version": CACHE_VERSION,
            ENVELOPE_KEY: {
                "text": response.text,
                "model": response.model,
                "tokens_in": response.tokens_in,
                "tokens_out": response.tokens_out,
            },
        }
        self._atomic_write_json(self.path_for(key), envelope)

    def get_entry(self, key: str) -> L2Entry | None:
        path = self.path_for(key)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            r = payload[ENVELOPE_KEY]
            return L2Entry(
                cached_at=datetime.fromisoformat(payload["cached_at"]),
                cache_version=str(payload["cache_version"]),
                response=CachedLLMResponse(
                    text=str(r["text"]),
                    model=str(r["model"]),
                    tokens_in=int(r["tokens_in"]),
                    tokens_out=int(r["tokens_out"]),
                ),
            )
        except (json.JSONDecodeError, KeyError, ValueError, TypeError):
            return None

    def clear(self) -> int:
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


__all__ = ["CachedLLMResponse", "L2Entry", "ResponseCache"]
