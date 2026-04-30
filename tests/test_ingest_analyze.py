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

    # Verify the model was called with temperature=0 (cacheability invariant).
    assert stub_client.calls[0]["temperature"] == 0
    assert "wiki_purpose" in stub_client.calls[0]["system"]


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
