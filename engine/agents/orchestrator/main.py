"""Orchestrator agent (design §7.1, §10A).

A small Sonnet 4.6 agent that routes one user request to either the
ingest subagent (for paths/URLs) or the QA subagent (for questions).
Subagents run in-process as Python functions invoked via the Messages
API ``tool_use`` loop — no nested CLI subprocesses.

Cheap by design: the orchestrator's job is dispatch + composition.
The subagents do the heavy lifting; the receipts cell tracks per-agent
token attribution.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from engine.agents.qa.gap_detection import qa_with_gap_detection
from engine.agents.qa.single_question import QaAnswer
from engine.prompts import Prompt, load_prompt
from engine.utils.api_compat import temperature_kwargs
from engine.utils.cost_tracker import estimate_cost_usd

if TYPE_CHECKING:
    from anthropic import Anthropic

PROMPT_NAME = "orchestrator"
DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_MAX_TOKENS = 1024
MAX_TURNS = 6


class SubagentCall(BaseModel):
    """One subagent invocation — what was called, what it returned."""

    model_config = ConfigDict(extra="forbid")

    agent: str
    input: dict
    output_summary: str
    tokens_in: int = Field(default=0, ge=0)
    tokens_out: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0.0, ge=0.0)
    wall_s: float = Field(default=0.0, ge=0.0)


class OrchestratorRun(BaseModel):
    """Result of one orchestrator dispatch."""

    model_config = ConfigDict(extra="forbid")

    user_input: str
    final_text: str
    subagent_calls: list[SubagentCall] = Field(default_factory=list)
    orchestrator_tokens_in: int = Field(default=0, ge=0)
    orchestrator_tokens_out: int = Field(default=0, ge=0)
    orchestrator_cost_usd: float = Field(default=0.0, ge=0.0)
    total_cost_usd: float = Field(default=0.0, ge=0.0)
    wall_s: float = Field(default=0.0, ge=0.0)


_TOOL_SCHEMAS = [
    {
        "name": "spawn_ingest_agent",
        "description": (
            "Run the ingest pipeline on a local file path or HTTP URL. "
            "Use when the user wants to add a source to the wiki."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "input": {
                    "type": "string",
                    "description": "A filesystem path or URL to ingest.",
                },
                "hint": {
                    "type": "string",
                    "description": "Optional synthesis hint for the ingest agent.",
                },
            },
            "required": ["input"],
        },
    },
    {
        "name": "spawn_qa_agent",
        "description": (
            "Run the QA agent on a question. Returns either an answer with "
            "citations or a structured KnowledgeGap when wiki coverage is thin."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The user's question, restated as a standalone query.",
                },
            },
            "required": ["question"],
        },
    },
]


async def _run_ingest_subagent(
    user_input: str,
    hint: str | None,
    *,
    config,
    client: Anthropic,
    wiki_root: Path,
) -> tuple[str, dict]:
    """Run extract → analyze → synthesize. Writes to wiki_root/sources/."""
    from engine.agents.ingest import analyze_source, synthesize_page
    from engine.agents.synthesis.cross_source import derive_default_path
    from engine.models.pages import SourceKind
    from engine.tools.upsert_page import upsert_page
    from engine.utils.dispatch import extract, extract_url

    is_url = user_input.startswith(("http://", "https://"))
    if is_url:
        extracted = await extract_url(user_input, client=client)
    else:
        extracted = extract(Path(user_input), client=client)

    if extracted.failure_reason:
        return f"extraction failed: {extracted.failure_reason}", {
            "extraction_method": extracted.extraction_method,
            "failure_reason": extracted.failure_reason,
        }

    source_kind = SourceKind.YOUTUBE_VIDEO if is_url else SourceKind.LOCAL_FILE
    analysis = await analyze_source(extracted.text, source_kind, config, client=client)
    page, body, _log = await synthesize_page(analysis, config, hint=hint, client=client)
    page_path = derive_default_path(page)

    upsert_page(
        page_path,
        page.model_dump(mode="json", exclude_none=True),
        body,
        wiki_root=wiki_root,
    )
    summary = f"ingested {user_input!r} → {page_path} ({page.status.value})"
    return summary, {"path": page_path, "status": page.status.value, "title": page.title}


def _summarize_qa_result(result: QaAnswer | object) -> tuple[str, dict]:
    if isinstance(result, QaAnswer):
        return f"answered (citations={len(result.citations)})", {
            "type": "answer",
            "citations": result.citations,
            "answer_preview": result.answer[:200],
        }
    # KnowledgeGap path
    return (
        f"knowledge gap detected ({len(getattr(result, 'suggested_ingests', []))} suggestions)",
        {
            "type": "knowledge_gap",
            "reason": getattr(result, "reason", ""),
            "suggested_ingests": list(getattr(result, "suggested_ingests", [])),
        },
    )


async def run_orchestrator(
    user_input: str,
    *,
    wiki_root: Path,
    config,
    client: Anthropic | None = None,
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    prompt: Prompt | None = None,
    max_turns: int = MAX_TURNS,
) -> OrchestratorRun:
    """Dispatch one user request through the orchestrator + subagent loop."""
    if client is None:
        from anthropic import Anthropic as _Anthropic

        client = _Anthropic()

    prompt = prompt or load_prompt(PROMPT_NAME)
    user_msg = prompt.user_template.format(user_input=user_input)
    messages: list[dict] = [{"role": "user", "content": user_msg}]
    subagent_calls: list[SubagentCall] = []
    final_text_parts: list[str] = []
    total_in = 0
    total_out = 0
    t0 = time.monotonic()

    for _turn in range(max_turns):
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            **temperature_kwargs(model),
            tools=_TOOL_SCHEMAS,
            system=prompt.system,
            messages=messages,
        )
        total_in += resp.usage.input_tokens
        total_out += resp.usage.output_tokens
        messages.append({"role": "assistant", "content": resp.content})

        if resp.stop_reason == "end_turn":
            for block in resp.content:
                if getattr(block, "type", None) == "text":
                    final_text_parts.append(block.text)
            break

        tool_results: list[dict] = []
        for block in resp.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            sub_t0 = time.monotonic()
            tokens_in_sub = 0
            tokens_out_sub = 0
            cost_sub = 0.0
            try:
                if block.name == "spawn_ingest_agent":
                    summary, _ = await _run_ingest_subagent(
                        block.input["input"],
                        block.input.get("hint"),
                        config=config,
                        client=client,
                        wiki_root=wiki_root,
                    )
                elif block.name == "spawn_qa_agent":
                    qa_result = await qa_with_gap_detection(
                        block.input["question"],
                        wiki_root=wiki_root,
                        client=client,
                    )
                    summary, _ = _summarize_qa_result(qa_result)
                    tokens_in_sub = getattr(qa_result, "tokens_in", 0)
                    tokens_out_sub = getattr(qa_result, "tokens_out", 0)
                    cost_sub = getattr(qa_result, "cost_usd", 0.0)
                else:
                    summary = f"unknown tool: {block.name}"
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": summary,
                        "is_error": False,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                summary = f"{type(exc).__name__}: {exc}"
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": summary,
                        "is_error": True,
                    }
                )

            subagent_calls.append(
                SubagentCall(
                    agent=block.name,
                    input=block.input,
                    output_summary=summary,
                    tokens_in=tokens_in_sub,
                    tokens_out=tokens_out_sub,
                    cost_usd=cost_sub,
                    wall_s=time.monotonic() - sub_t0,
                )
            )
        messages.append({"role": "user", "content": tool_results})

    orchestrator_cost = estimate_cost_usd(model, total_in, total_out)
    subagent_cost = sum(c.cost_usd for c in subagent_calls)

    return OrchestratorRun(
        user_input=user_input,
        final_text="".join(final_text_parts).strip(),
        subagent_calls=subagent_calls,
        orchestrator_tokens_in=total_in,
        orchestrator_tokens_out=total_out,
        orchestrator_cost_usd=orchestrator_cost,
        total_cost_usd=orchestrator_cost + subagent_cost,
        wall_s=time.monotonic() - t0,
    )


__all__ = [
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_MODEL",
    "MAX_TURNS",
    "PROMPT_NAME",
    "OrchestratorRun",
    "SubagentCall",
    "run_orchestrator",
]
