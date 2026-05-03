"""Model-comparison harness for end-to-end ingest runs.

`run_one` executes ingest analyze + synthesize for one fixture and one
`(analyze_model, synth_model)` pair, returning a typed receipt with
tokens, attempts, cost, latency, and rendered outputs.

Design choices implemented here:
- Identical prompts across compared runs: prompts are loaded once and
  cached so all model pairs use the same prompt objects.
- Cost uses `record_attempt`: no duplicated pricing math.
"""

from __future__ import annotations

import time
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from engine.agents.ingest import SourceAnalysis, analyze_source, synthesize_page
from engine.models.pages import PageStatus, SourceKind, SourcePage
from engine.prompts import Prompt, load_prompt
from engine.utils.cost_tracker import record_attempt

if TYPE_CHECKING:
    from anthropic import Anthropic

    from engine.models.wiki_config import MarginaliaConfig


class ModelRunResult(BaseModel):
    """Receipt for one ingest run over a fixture/model pair."""

    model_config = ConfigDict(extra="forbid")

    fixture: str
    analyze_model: str
    synth_model: str
    analyze_tokens_in: int = Field(ge=0)
    analyze_tokens_out: int = Field(ge=0)
    synth_attempts: int = Field(ge=0)
    synth_tokens_in: int = Field(ge=0)
    synth_tokens_out: int = Field(ge=0)
    cost_usd: float = Field(ge=0.0)
    wall_s: float = Field(ge=0.0)
    final_status: PageStatus
    page: SourcePage
    body: str = Field(default="", description="Synthesized markdown body; empty on draft fallback.")
    analysis: SourceAnalysis


class _RecordingMessages:
    def __init__(self, inner_messages, sink: list[dict]) -> None:
        self._inner_messages = inner_messages
        self._sink = sink

    def create(self, **kwargs):
        resp = self._inner_messages.create(**kwargs)
        usage = getattr(resp, "usage", None)
        self._sink.append(
            {
                "model": kwargs.get("model"),
                "tokens_in": int(getattr(usage, "input_tokens", 0) or 0),
                "tokens_out": int(getattr(usage, "output_tokens", 0) or 0),
            }
        )
        return resp


class _RecordingClient:
    def __init__(self, client) -> None:
        self._client = client
        self.calls: list[dict] = []
        self.messages = _RecordingMessages(client.messages, self.calls)


@lru_cache(maxsize=1)
def _comparison_prompts() -> tuple[Prompt, Prompt]:
    """Load once so all compared runs use identical prompt objects."""
    return load_prompt("ingest_analyze"), load_prompt("ingest_synthesize")


async def run_one(
    fixture: Path,
    analyze_model: str,
    synth_model: str,
    config: MarginaliaConfig,
    *,
    client: Anthropic,
) -> ModelRunResult:
    """Run ingest end-to-end for one fixture and model pair."""
    t0 = time.monotonic()
    fixture_path = Path(fixture)
    content = fixture_path.read_text(encoding="utf-8")
    analyze_prompt, synth_prompt = _comparison_prompts()

    recorder = _RecordingClient(client)

    analysis = await analyze_source(
        content,
        SourceKind.LOCAL_FILE,
        config,
        client=recorder,
        prompt=analyze_prompt,
        model=analyze_model,
    )
    if not recorder.calls:
        raise RuntimeError("analyze step produced no model call usage")

    analyze_usage = recorder.calls[0]
    analyze_tokens_in = int(analyze_usage["tokens_in"])
    analyze_tokens_out = int(analyze_usage["tokens_out"])

    page, body, synth_log = await synthesize_page(
        analysis,
        config,
        client=recorder,
        prompt=synth_prompt,
        model=synth_model,
    )

    synth_tokens_in = sum(int(entry.get("input_tokens", 0)) for entry in synth_log)
    synth_tokens_out = sum(int(entry.get("output_tokens", 0)) for entry in synth_log)
    synth_attempts = len(synth_log)

    cost_records = [
        record_attempt(
            agent="ingest_analyze",
            model=analyze_model,
            tokens_in=analyze_tokens_in,
            tokens_out=analyze_tokens_out,
        )
    ]
    cost_records.extend(
        record_attempt(
            agent="ingest_synthesize",
            model=synth_model,
            tokens_in=int(entry.get("input_tokens", 0)),
            tokens_out=int(entry.get("output_tokens", 0)),
        )
        for entry in synth_log
    )

    return ModelRunResult(
        fixture=str(fixture_path),
        analyze_model=analyze_model,
        synth_model=synth_model,
        analyze_tokens_in=analyze_tokens_in,
        analyze_tokens_out=analyze_tokens_out,
        synth_attempts=synth_attempts,
        synth_tokens_in=synth_tokens_in,
        synth_tokens_out=synth_tokens_out,
        cost_usd=sum(record.cost_usd for record in cost_records),
        wall_s=time.monotonic() - t0,
        final_status=page.status,
        page=page,
        body=body,
        analysis=analysis,
    )


__all__ = ["ModelRunResult", "run_one"]
