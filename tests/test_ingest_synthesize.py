"""synthesize_page retry contract (design §7.3.1)."""

import json
from datetime import date

import pytest

from engine.agents.ingest import (
    MAX_ATTEMPTS,
    SourceAnalysis,
    normalize_errors,
    parse_frontmatter_and_body,
    synthesize_page,
)
from engine.models.pages import PageStatus, PageType, SourceKind


@pytest.fixture
def sample_analysis() -> SourceAnalysis:
    return SourceAnalysis(
        proposed_title="Apollo Launch Blockers Sync",
        proposed_type=PageType.MEETING,
        summary="A faithful meeting summary that satisfies the twenty-character minimum.",
        entities=["Sandro", "Priya"],
        proposed_tags=["apollo"],
        source_kind=SourceKind.LOCAL_FILE,
        content_sha256="a" * 64,
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


@pytest.mark.asyncio
async def test_happy_path_one_attempt(stub_client, minimal_config, sample_analysis):
    stub_client.texts = [_valid_source_page_response(sample_analysis.content_sha256)]

    page, log = await synthesize_page(sample_analysis, minimal_config, client=stub_client)

    assert page.status == PageStatus.ACTIVE
    assert page.type == PageType.SOURCE
    assert not page.validation_errors
    assert len(log) == 1
    assert "validation_errors" not in log[0]


@pytest.mark.asyncio
async def test_retry_recovers_after_validation_error(stub_client, minimal_config, sample_analysis):
    bad_fm = {
        "title": "Apollo",
        "type": "source",
        "status": "active",
        "created": "2026-04-30",
        "last_synced": "2026-04-30",
        # Invalid wikilink (bare name) on owners — triggers ValidationError.
        "owners": ["Sandro"],
        "sources": [
            {
                "ref": sample_analysis.content_sha256,
                "kind": "local_file",
                "captured": "2026-04-30",
                "authority": "canonical",
            }
        ],
    }
    stub_client.texts = [
        f"<frontmatter>{json.dumps(bad_fm)}</frontmatter><body>x</body>",
        _valid_source_page_response(sample_analysis.content_sha256),
    ]

    page, log = await synthesize_page(sample_analysis, minimal_config, client=stub_client)

    assert page.status == PageStatus.ACTIVE
    assert len(log) == 2
    assert "validation_errors" in log[0]
    # Confirm the retry user message carried the prior errors back to the model.
    second_call_user = stub_client.calls[1]["messages"][0]["content"]
    assert "<validation_errors>" in second_call_user
    assert "wikilinks must match" in second_call_user


@pytest.mark.asyncio
async def test_draft_fallback_after_max_attempts(stub_client, minimal_config, sample_analysis):
    # Every response is unparseable → forces 3 failures → draft fallback.
    stub_client.texts = ["this response has no frontmatter or body tags"]

    page, log = await synthesize_page(sample_analysis, minimal_config, client=stub_client)

    assert page.status == PageStatus.DRAFT
    assert page.validation_errors
    assert page.title == sample_analysis.proposed_title
    assert page.created == date.today()
    assert len(log) == MAX_ATTEMPTS
    assert all("validation_errors" in entry for entry in log)


def test_parse_frontmatter_and_body_round_trip():
    raw = '<frontmatter>{"a": 1}</frontmatter>\n<body>\nhello\n</body>'
    fm, body = parse_frontmatter_and_body(raw)
    assert fm == {"a": 1}
    assert body == "hello"


def test_parse_frontmatter_missing_tags_raises():
    with pytest.raises(ValueError, match="must include"):
        parse_frontmatter_and_body("just some text")


def test_parse_frontmatter_bad_json_raises():
    with pytest.raises(ValueError, match="malformed <frontmatter>"):
        parse_frontmatter_and_body("<frontmatter>{not json}</frontmatter><body>x</body>")


def test_normalize_errors_value_error():
    out = normalize_errors(ValueError("nope"))
    assert out == [{"loc": ["response"], "msg": "nope", "type": "value_error"}]


def test_normalize_errors_json_decode():
    try:
        json.loads("{")
    except json.JSONDecodeError as exc:
        out = normalize_errors(exc)
        assert out[0]["type"] == "json_decode_error"
        assert out[0]["loc"] == ["response"]
