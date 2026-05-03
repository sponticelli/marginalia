"""Cross-source synthesis — strict-schema retry + citation verification."""

from __future__ import annotations

import json
from datetime import date

import pytest

from engine.agents.synthesis import (
    derive_default_path,
    extract_wikilinks,
    synthesize_cross_source,
    verify_citations,
)
from engine.models.pages import (
    AnalysisPage,
    ConceptPage,
    PageStatus,
    PageType,
    SourceKind,
    SourcePage,
    SourceRef,
)


@pytest.fixture
def three_sources() -> list[SourcePage]:
    """Three SourcePage fixtures the cross-source agent will synthesize across."""
    today = date(2026, 5, 3)
    base_kwargs = dict(
        type=PageType.SOURCE,
        status=PageStatus.ACTIVE,
        created=today,
        last_synced=today,
    )

    def _ref(name: str) -> SourceRef:
        return SourceRef(
            ref=name, kind=SourceKind.LOCAL_FILE, captured=today, authority="canonical"
        )

    return [
        SourcePage(title="Apollo Launch Sync", **base_kwargs, sources=[_ref("good_source.md")]),
        SourcePage(title="Marginalia Engine Reference", **base_kwargs, sources=[_ref("clean.pdf")]),
        SourcePage(
            title="Pipeline Architecture", **base_kwargs, sources=[_ref("architecture.png")]
        ),
    ]


def _concept_response(
    related: list[str],
    body_links: list[str] = None,
    *,
    title: str = "Marginalia Pipeline Synthesis",
    confidence: str = "medium",
    contradicts: list[str] | None = None,
    extra_thinking: bool = True,
) -> str:
    """Build a stub model response with thinking + frontmatter + body."""
    fm: dict = {
        "title": title,
        "type": "concept",
        "status": "active",
        "confidence": confidence,
        "created": "2026-05-03",
        "last_synced": "2026-05-03",
        "related": related,
    }
    if contradicts:
        fm["contradicts"] = contradicts
    body_links = body_links or []
    body_links_md = " ".join(f"[[{p}]]" for p in body_links)
    body = f"This concept synthesizes findings across {body_links_md}."
    thinking = (
        "<thinking>Plan: identify the unifying theme and cite all sources.</thinking>\n"
        if extra_thinking
        else ""
    )
    return f"{thinking}<frontmatter>{json.dumps(fm)}</frontmatter>\n<body>\n{body}\n</body>"


def _all_three_paths(sources: list[SourcePage]) -> list[str]:
    return [derive_default_path(s) for s in sources]


@pytest.mark.asyncio
async def test_cross_source_happy_path(stub_client, minimal_config, three_sources) -> None:
    paths = _all_three_paths(three_sources)
    related = [f"[[{p}]]" for p in paths]
    stub_client.texts = [_concept_response(related, body_links=paths)]

    page, body, log = await synthesize_cross_source(
        three_sources, minimal_config, client=stub_client
    )
    assert isinstance(page, ConceptPage)
    assert page.status == PageStatus.ACTIVE
    assert len(log) == 1
    assert "validation_errors" not in log[0]
    assert "synthesizes findings" in body


@pytest.mark.asyncio
async def test_cross_source_retries_on_pydantic_error(
    stub_client, minimal_config, three_sources
) -> None:
    """First response has invalid wikilink format in `related`; retry succeeds."""
    paths = _all_three_paths(three_sources)
    bad_related = ["BadCase"]  # not wrapped in [[...]] → fails wikilink_str validator
    good_related = [f"[[{p}]]" for p in paths]
    stub_client.texts = [
        _concept_response(bad_related, body_links=paths),
        _concept_response(good_related, body_links=paths),
    ]

    page, _body, log = await synthesize_cross_source(
        three_sources, minimal_config, client=stub_client
    )
    assert page.status == PageStatus.ACTIVE
    assert len(log) == 2
    assert "validation_errors" in log[0]
    second_user_msg = stub_client.calls[1]["messages"][0]["content"]
    assert "<validation_errors>" in second_user_msg


@pytest.mark.asyncio
async def test_cross_source_retries_on_dangling_citation(
    stub_client, minimal_config, three_sources
) -> None:
    """First response cites a non-existent path; retry uses only inputs."""
    paths = _all_three_paths(three_sources)
    dangling_related = [f"[[{p}]]" for p in paths] + ["[[knowledge/nonexistent]]"]
    good_related = [f"[[{p}]]" for p in paths]
    stub_client.texts = [
        _concept_response(dangling_related, body_links=paths),
        _concept_response(good_related, body_links=paths),
    ]

    page, _body, log = await synthesize_cross_source(
        three_sources, minimal_config, client=stub_client
    )
    assert page.status == PageStatus.ACTIVE
    assert len(log) == 2
    assert log[0]["validation_errors"][0]["type"] == "dangling_citation"
    second_user_msg = stub_client.calls[1]["messages"][0]["content"]
    assert "dangling_citation" in second_user_msg


@pytest.mark.asyncio
async def test_cross_source_retries_on_missing_source_citation(
    stub_client, minimal_config, three_sources
) -> None:
    """Schema-valid but only 2 of 3 sources cited; retry covers all three."""
    paths = _all_three_paths(three_sources)
    only_two = [f"[[{paths[0]}]]", f"[[{paths[1]}]]"]
    all_three = [f"[[{p}]]" for p in paths]
    stub_client.texts = [
        _concept_response(only_two, body_links=[paths[0], paths[1]]),
        _concept_response(all_three, body_links=paths),
    ]

    page, _body, log = await synthesize_cross_source(
        three_sources,
        minimal_config,
        client=stub_client,
        require_all_sources_cited=True,
    )
    assert page.status == PageStatus.ACTIVE
    assert len(log) == 2
    err_types = {e["type"] for e in log[0]["validation_errors"]}
    assert "missing_source_citation" in err_types


@pytest.mark.asyncio
async def test_cross_source_draft_fallback_after_max_attempts(
    stub_client, minimal_config, three_sources
) -> None:
    """All three responses unparseable → draft fallback with validation_errors."""
    stub_client.texts = ["totally malformed response with no tags"]

    page, body, log = await synthesize_cross_source(
        three_sources, minimal_config, client=stub_client
    )
    assert page.status == PageStatus.DRAFT
    assert page.validation_errors
    assert len(log) == 3
    assert body == ""


@pytest.mark.asyncio
async def test_cross_source_target_type_analysis(
    stub_client, minimal_config, three_sources
) -> None:
    """target_type=ANALYSIS validates against AnalysisPage; type=concept fails."""
    paths = _all_three_paths(three_sources)
    related = [f"[[{p}]]" for p in paths]
    today = date(2026, 5, 3)
    fm = {
        "title": "Pipeline Analysis",
        "type": "analysis",
        "status": "active",
        "confidence": "medium",
        "created": today.isoformat(),
        "last_synced": today.isoformat(),
        "related": related,
        "sources": [
            {
                "ref": "synth-derived",
                "kind": "local_file",
                "captured": today.isoformat(),
                "authority": "canonical",
            }
        ],
    }
    body_links = " ".join(f"[[{p}]]" for p in paths)
    stub_client.texts = [
        f"<thinking>plan</thinking>\n<frontmatter>{json.dumps(fm)}</frontmatter>\n<body>cites {body_links}</body>"
    ]

    page, _body, _log = await synthesize_cross_source(
        three_sources,
        minimal_config,
        target_type=PageType.ANALYSIS,
        client=stub_client,
    )
    assert isinstance(page, AnalysisPage)
    assert page.type == PageType.ANALYSIS
    assert page.confidence is not None


@pytest.mark.asyncio
async def test_cross_source_hint_propagates(stub_client, minimal_config, three_sources) -> None:
    paths = _all_three_paths(three_sources)
    related = [f"[[{p}]]" for p in paths]
    stub_client.texts = [_concept_response(related, body_links=paths)]

    await synthesize_cross_source(
        three_sources,
        minimal_config,
        hint="frame around platform engineering",
        client=stub_client,
    )
    user_msg = stub_client.calls[0]["messages"][0]["content"]
    assert "<hint>" in user_msg
    assert "platform engineering" in user_msg


def test_extract_wikilinks_returns_all_inner_paths() -> None:
    text = "see [[sources/a]] and [[knowledge/b/c]] but not [name] or {curly}"
    assert extract_wikilinks(text) == ["sources/a", "knowledge/b/c"]


def test_verify_citations_reports_dangling_and_missing(three_sources) -> None:
    paths = _all_three_paths(three_sources)
    available = set(paths)
    today = date(2026, 5, 3)
    page = ConceptPage(
        title="x",
        type=PageType.CONCEPT,
        status=PageStatus.ACTIVE,
        confidence="medium",
        created=today,
        last_synced=today,
        related=[f"[[{paths[0]}]]", "[[knowledge/nonexistent]]"],
    )
    body = f"only cites [[{paths[0]}]]"  # missing paths[1] and paths[2]
    errors = verify_citations(
        page,
        body,
        available,
        require_all_sources_cited=True,
        sources_paths=paths,
    )
    types = [e["type"] for e in errors]
    assert "dangling_citation" in types
    assert types.count("missing_source_citation") == 2


def test_derive_default_path_slugifies_punctuation(three_sources) -> None:
    today = date(2026, 5, 3)
    page = SourcePage(
        title="Q2 — Apollo!! Launch / Sync",
        type=PageType.SOURCE,
        status=PageStatus.ACTIVE,
        created=today,
        last_synced=today,
        sources=[
            SourceRef(ref="x", kind=SourceKind.LOCAL_FILE, captured=today, authority="canonical")
        ],
    )
    path = derive_default_path(page)
    assert path == "sources/q2-apollo-launch-sync"
    inner = path.replace("sources/", "sources/")
    # Validate it would survive the wikilink regex.
    import re

    assert re.match(r"^[a-z0-9][a-z0-9/_-]*$", inner)
