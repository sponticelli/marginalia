"""Prompt loader contract tests — no API calls."""

from pathlib import Path

import pytest

from engine.prompts import Prompt, PromptLoadError, load_prompt


def test_loads_ingest_analyze():
    p = load_prompt("ingest_analyze")
    assert isinstance(p, Prompt)
    assert p.name == "ingest_analyze"
    assert p.version == "v1"
    assert p.role == "ingest.analyze"
    assert p.model == "claude-haiku-4-5"
    assert "Marginalia ingest analyzer" in p.system
    assert "{source_kind}" in p.user_template
    assert "{schema_json}" in p.user_template


def test_loads_ingest_synthesize():
    p = load_prompt("ingest_synthesize")
    assert p.version == "v1"
    assert p.model == "claude-sonnet-4-6"
    assert "<frontmatter>" in p.system
    assert "{analysis_json}" in p.user_template


def test_missing_file_raises(tmp_path: Path):
    with pytest.raises(PromptLoadError, match="prompt file not found"):
        load_prompt("does_not_exist", prompts_dir=tmp_path)


def test_missing_version_raises(tmp_path: Path):
    (tmp_path / "no_version.md").write_text(
        "---\nname: x\n---\n\n## System\nfoo\n\n## User Template\nbar\n",
        encoding="utf-8",
    )
    with pytest.raises(PromptLoadError, match="missing required `version`"):
        load_prompt("no_version", prompts_dir=tmp_path)


def test_missing_sections_raises(tmp_path: Path):
    (tmp_path / "no_sections.md").write_text(
        "---\nversion: v1\n---\n\nbody without sections\n",
        encoding="utf-8",
    )
    with pytest.raises(PromptLoadError, match="must contain"):
        load_prompt("no_sections", prompts_dir=tmp_path)
