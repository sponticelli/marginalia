"""Orchestrator routing tests — ingest vs QA dispatch under tool_use."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest


@dataclass
class _StubToolUseBlock:
    type: str
    name: str
    input: dict
    id: str


@dataclass
class _StubTextBlock:
    type: str
    text: str


@dataclass
class _StubUsage:
    input_tokens: int = 50
    output_tokens: int = 30


@dataclass
class _StubResp:
    content: list
    usage: _StubUsage
    stop_reason: str


class StubOrchClient:
    """Minimal Anthropic stand-in that supports tool_use blocks."""

    def __init__(self, scripted: list[_StubResp]) -> None:
        self._scripted = list(scripted)
        self.calls: list[dict] = []
        self.messages = self  # so client.messages.create works

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._scripted.pop(0)


@pytest.mark.asyncio
async def test_orchestrator_routes_qa_for_question(minimal_config, tmp_path: Path) -> None:
    """A question prompt → orchestrator emits a spawn_qa_agent tool_use."""
    from engine.agents.orchestrator import run_orchestrator

    qa_response = _StubResp(
        content=[
            _StubToolUseBlock(
                type="tool_use",
                name="spawn_qa_agent",
                input={"question": "what about pricing?"},
                id="tool_1",
            )
        ],
        usage=_StubUsage(),
        stop_reason="tool_use",
    )
    # qa subagent's gap-signal call (no pages in tmp_path → KnowledgeGap branch).
    gap_response = _StubResp(
        content=[
            _StubTextBlock(
                type="text",
                text=json.dumps({"reason": "no pages", "suggested_ingests": ["search:pricing"]}),
            )
        ],
        usage=_StubUsage(),
        stop_reason="end_turn",
    )
    final_response = _StubResp(
        content=[_StubTextBlock(type="text", text="No coverage; suggested ingests below.")],
        usage=_StubUsage(),
        stop_reason="end_turn",
    )
    client = StubOrchClient([qa_response, gap_response, final_response])

    run = await run_orchestrator(
        "tell me about pricing",
        wiki_root=tmp_path,
        config=minimal_config,
        client=client,
    )
    assert any(c.agent == "spawn_qa_agent" for c in run.subagent_calls)
    assert run.final_text == "No coverage; suggested ingests below."


@pytest.mark.asyncio
async def test_orchestrator_returns_attempt_log(minimal_config, tmp_path: Path) -> None:
    """Each subagent invocation gets recorded with input + output summary."""
    from engine.agents.orchestrator import run_orchestrator

    end_turn = _StubResp(
        content=[_StubTextBlock(type="text", text="answered.")],
        usage=_StubUsage(),
        stop_reason="end_turn",
    )
    client = StubOrchClient([end_turn])

    run = await run_orchestrator(
        "say hello",  # no tool calls expected
        wiki_root=tmp_path,
        config=minimal_config,
        client=client,
    )
    assert run.subagent_calls == []
    assert run.orchestrator_tokens_in == 50
    assert run.orchestrator_tokens_out == 30
    assert run.orchestrator_cost_usd > 0


@pytest.mark.asyncio
async def test_orchestrator_logs_tool_call_metadata(minimal_config, tmp_path: Path) -> None:
    from engine.agents.orchestrator import run_orchestrator

    qa_call = _StubResp(
        content=[
            _StubToolUseBlock(
                type="tool_use",
                name="spawn_qa_agent",
                input={"question": "what about pricing?"},
                id="tool_a",
            )
        ],
        usage=_StubUsage(),
        stop_reason="tool_use",
    )
    qa_inner_response = _StubResp(
        content=[
            _StubTextBlock(type="text", text=json.dumps({"reason": "no", "suggested_ingests": []}))
        ],
        usage=_StubUsage(),
        stop_reason="end_turn",
    )
    final_response = _StubResp(
        content=[_StubTextBlock(type="text", text="done.")],
        usage=_StubUsage(),
        stop_reason="end_turn",
    )
    client = StubOrchClient([qa_call, qa_inner_response, final_response])

    run = await run_orchestrator(
        "what about pricing?",
        wiki_root=tmp_path,
        config=minimal_config,
        client=client,
    )
    assert len(run.subagent_calls) == 1
    call = run.subagent_calls[0]
    assert call.agent == "spawn_qa_agent"
    assert call.input == {"question": "what about pricing?"}
    assert call.wall_s >= 0
