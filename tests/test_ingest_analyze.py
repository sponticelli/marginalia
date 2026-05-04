"""analyze_source contract — content_sha256 is locally computed, model output validated."""

import json

import pytest

from engine.agents.ingest import (
    SourceAnalysis,
    analyze_source,
    compute_content_sha256,
)
from engine.models.pages import PageType, SourceKind


@pytest.mark.asyncio
async def test_analyze_source_validates_and_overrides_sha(stub_client, minimal_config):
    content = "Apollo launch sync notes.\nSandro and Priya reviewed blockers."
    expected_sha = compute_content_sha256(content)

    payload = {
        "proposed_title": "Apollo Launch Blockers Sync",
        "proposed_type": "meeting",
        "summary": "A meeting summary that is at least twenty characters long for validation.",
        "entities": ["Sandro", "Priya", "Apollo"],
        "proposed_tags": ["apollo", "launch"],
        "source_kind": "local_file",
        # Wrong sha on purpose — engine must override with locally-computed value.
        "content_sha256": "0" * 64,
    }
    stub_client.texts = [json.dumps(payload)]

    result = await analyze_source(
        content,
        SourceKind.LOCAL_FILE,
        minimal_config,
        client=stub_client,
    )

    assert isinstance(result, SourceAnalysis)
    assert result.proposed_type == PageType.MEETING
    assert result.content_sha256 == expected_sha
    assert result.content_sha256 != "0" * 64

    # `temperature=0` is passed for the default Haiku 4.5 model — needed for
    # L1 cache determinism. See engine.utils.api_compat.temperature_kwargs.
    assert stub_client.calls[0]["temperature"] == 0
    # System is sent as cacheable content blocks by default (L3, design §7.6);
    # check the textual payload regardless of form.
    system = stub_client.calls[0]["system"]
    system_text = system if isinstance(system, str) else "".join(b["text"] for b in system)
    assert "wiki_purpose" in system_text
    if isinstance(system, list):
        assert system[-1].get("cache_control") == {"type": "ephemeral"}


@pytest.mark.asyncio
async def test_analyze_source_skips_temperature_for_opus(stub_client, minimal_config):
    """Opus 4.7 deprecated `temperature`; the helper must omit it for that model."""
    content = "A short note."
    stub_client.texts = [
        json.dumps(
            {
                "proposed_title": "Note",
                "proposed_type": "source",
                "summary": "A faithful summary that satisfies the twenty-character minimum.",
                "entities": [],
                "proposed_tags": [],
                "source_kind": "local_file",
                "content_sha256": "0" * 64,
            }
        )
    ]
    await analyze_source(
        content,
        SourceKind.LOCAL_FILE,
        minimal_config,
        client=stub_client,
        model="claude-opus-4-7",
    )
    assert "temperature" not in stub_client.calls[0]
    assert stub_client.calls[0]["model"] == "claude-opus-4-7"


@pytest.mark.asyncio
async def test_analyze_source_handles_fenced_json(stub_client, minimal_config):
    """Model sometimes wraps JSON in ```json fences — parser must extract it."""
    content = "Some content here that is long enough to summarize."
    payload = {
        "proposed_title": "T",
        "proposed_type": "source",
        "summary": "A faithful summary that satisfies the twenty-character minimum.",
        "entities": [],
        "proposed_tags": [],
        "source_kind": "local_file",
        "content_sha256": "0" * 64,
    }
    stub_client.texts = [f"```json\n{json.dumps(payload)}\n```"]

    result = await analyze_source(
        content, SourceKind.LOCAL_FILE, minimal_config, client=stub_client
    )
    assert result.proposed_title == "T"
