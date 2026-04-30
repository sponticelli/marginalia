"""Cacheable ingest analyze step (design §7.3, §7.6 L1).

`analyze_source` is the ingest step-1 contract: a deterministic,
prompt-versioned, pure function over source content. Its output
(`SourceAnalysis`) intentionally carries bare entity names, not
wikilinks — wikilink resolution is state-dependent and belongs to
synthesize. Keeping these split is what makes the analyze layer
cacheable on `sha256(content) + CACHE_VERSION + prompt.version`.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from engine.models.pages import Confidence, PageType, SourceKind
from engine.prompts import Prompt, load_prompt

if TYPE_CHECKING:
    from anthropic import Anthropic

    from engine.models.wiki_config import MarginaliaConfig

PROMPT_NAME = "ingest_analyze"
DEFAULT_MODEL = "claude-haiku-4-5"
DEFAULT_MAX_TOKENS = 1024


class SourceAnalysis(BaseModel):
    """Structured output from the cacheable analyze step."""

    model_config = ConfigDict(extra="forbid")

    proposed_title: str = Field(
        min_length=1,
        description="Candidate title inferred from the source content.",
    )
    proposed_type: PageType = Field(
        description="Candidate wiki page type inferred from the source.",
    )
    summary: str = Field(
        min_length=20,
        max_length=2000,
        description="Faithful summary of the source content.",
    )
    entities: list[str] = Field(
        default_factory=list,
        description=(
            "Bare entity names extracted from content; wikilink resolution belongs to synthesize."
        ),
    )
    proposed_tags: list[str] = Field(
        default_factory=list,
        description="Candidate tags inferred from source content.",
    )
    source_kind: SourceKind = Field(
        description="Adapter/source kind that produced this analysis.",
    )
    confidence: Confidence | None = Field(
        default=None,
        description="Optional confidence for the extracted analysis.",
    )
    content_sha256: str = Field(
        pattern=r"^[a-f0-9]{64}$",
        description="SHA-256 digest of original source content; cache key spine.",
    )


def compute_content_sha256(content: str) -> str:
    """Return deterministic SHA-256 digest for analyze-step caching."""
    return hashlib.sha256(content.encode()).hexdigest()


def _parse_json_object(raw: str) -> dict:
    """Extract and parse the first JSON object from model text output."""
    text = raw.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        return json.loads(text[start : end + 1])


def build_analyze_system(prompt: Prompt, config: MarginaliaConfig) -> str:
    """Compose the analyze system prompt with the wiki's purpose + style guide.

    Kept as a pure helper so the cache layer can hash it deterministically.
    """
    return (
        prompt.system
        + f"\n\n<wiki_purpose>\n{config.purpose_body}\n</wiki_purpose>"
        + f"\n\n<style_guide>\n{config.agents_body}\n</style_guide>"
    )


async def analyze_source(
    content: str,
    source_kind: SourceKind,
    config: MarginaliaConfig,
    *,
    client: Anthropic | None = None,
    prompt: Prompt | None = None,
    model: str | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> SourceAnalysis:
    """Run the cacheable ingest analyze step on one source body.

    Deterministic (`temperature=0`) and pure over
    `(content, source_kind, prompt.version)` — safe to L1-cache on
    `sha256(content) + CACHE_VERSION + prompt.version`.

    `client` and `prompt` are injected for testability; both default to
    real Anthropic / on-disk prompt loading. The content hash is computed
    here (never trusted from the model) so it can serve as the cache key.
    """
    prompt = prompt or load_prompt(PROMPT_NAME)
    if client is None:
        from anthropic import Anthropic as _Anthropic

        client = _Anthropic()

    sha = compute_content_sha256(content)
    user_msg = prompt.user_template.format(
        source_kind=source_kind.value,
        sha=sha,
        content=content,
        schema_json=json.dumps(SourceAnalysis.model_json_schema()),
    )
    system = build_analyze_system(prompt, config)

    resp = client.messages.create(
        model=model or prompt.model or DEFAULT_MODEL,
        max_tokens=max_tokens,
        temperature=0,
        system=system,
        messages=[{"role": "user", "content": user_msg}],
    )
    data = _parse_json_object(resp.content[0].text)
    data["content_sha256"] = sha
    return SourceAnalysis.model_validate(data)


__all__ = [
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_MODEL",
    "PROMPT_NAME",
    "SourceAnalysis",
    "analyze_source",
    "build_analyze_system",
    "compute_content_sha256",
]
