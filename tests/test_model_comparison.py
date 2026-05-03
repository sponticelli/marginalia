"""Tests for end-to-end model comparison harness."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.utils.model_comparison import run_one


def _analyze_payload(*, source_kind: str = "local_file") -> str:
    return json.dumps(
        {
            "proposed_title": "Apollo Launch Blockers Sync",
            "proposed_type": "meeting",
            "summary": "A meeting summary that is at least twenty characters long for validation.",
            "entities": ["Sandro", "Priya", "Apollo"],
            "proposed_tags": ["apollo", "launch"],
            "source_kind": source_kind,
            "content_sha256": "0" * 64,
        }
    )


def _valid_source_page_response(sha: str) -> str:
    fm = {
        "title": "Apollo Launch Sync",
        "type": "source",
        "status": "active",
        "created": "2026-04-30",
        "last_synced": "2026-04-30",
        "sources": [
            {
                "ref": sha,
                "kind": "local_file",
                "captured": "2026-04-30",
                "authority": "canonical",
            }
        ],
        "tags": ["apollo"],
    }
    return f"<frontmatter>{json.dumps(fm)}</frontmatter>\n<body>\n# Apollo\n</body>"


def _invalid_source_page_response() -> str:
    return "this response has no frontmatter or body tags"


@pytest.mark.asyncio
async def test_run_one_happy_path(stub_client, minimal_config, tmp_path: Path):
    fixture = tmp_path / "good_source.md"
    fixture.write_text("Apollo launch sync notes.", encoding="utf-8")

    # Analyze (1) then synthesize (1).
    stub_client.texts = [_analyze_payload(), _valid_source_page_response("a" * 64)]

    result = await run_one(
        fixture,
        analyze_model="claude-haiku-4-5",
        synth_model="claude-sonnet-4-6",
        config=minimal_config,
        client=stub_client,
    )

    assert result.fixture == str(fixture)
    assert result.analyze_model == "claude-haiku-4-5"
    assert result.synth_model == "claude-sonnet-4-6"
    assert result.analyze_tokens_in == 100
    assert result.analyze_tokens_out == 20
    assert result.synth_attempts == 1
    assert result.synth_tokens_in == 100
    assert result.synth_tokens_out == 20
    # 100/20 Haiku + 100/20 Sonnet = 0.0002 + 0.0006 = 0.0008
    assert result.cost_usd == pytest.approx(0.0008)
    assert result.wall_s >= 0.0
    assert result.final_status == "active"
    assert result.body == "# Apollo"


@pytest.mark.asyncio
async def test_run_one_counts_retry_attempts(stub_client, minimal_config, tmp_path: Path):
    fixture = tmp_path / "garbage_source.md"
    fixture.write_text("Noisy source content.", encoding="utf-8")

    # Analyze (1) then synthesize retries (3 invalid responses).
    stub_client.texts = [
        _analyze_payload(),
        _invalid_source_page_response(),
        _invalid_source_page_response(),
        _invalid_source_page_response(),
    ]

    result = await run_one(
        fixture,
        analyze_model="claude-haiku-4-5",
        synth_model="claude-sonnet-4-6",
        config=minimal_config,
        client=stub_client,
    )

    assert result.synth_attempts == 3
    assert result.synth_tokens_in == 300
    assert result.synth_tokens_out == 60
    assert result.final_status == "draft"
    assert result.body == ""
    # 1 analyze + 3 synth calls at stub usage.
    assert result.cost_usd == pytest.approx(0.002)
