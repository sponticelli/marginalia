"""Strict-schema retry synthesize step (design §7.3.1).

The contract: validate frontmatter against the discriminated `Page`
union; on any `ValidationError` (or malformed response) feed the
errors back as `<validation_errors>...</validation_errors>` and retry.
After `MAX_ATTEMPTS` failures the page lands as `status: draft` with
`validation_errors[]` populated — never a silent commit.
"""

from __future__ import annotations

import json
import re
import time
from datetime import date
from typing import TYPE_CHECKING

from pydantic import ValidationError

from engine.agents.ingest.analyze import SourceAnalysis
from engine.models.pages import PageStatus, PageType, SourcePage
from engine.prompts import Prompt, load_prompt

if TYPE_CHECKING:
    from anthropic import Anthropic

    from engine.models.wiki_config import MarginaliaConfig

PROMPT_NAME = "ingest_synthesize"
DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_MAX_TOKENS = 2048
MAX_ATTEMPTS = 3

_FRONTMATTER_RE = re.compile(r"<frontmatter>\s*(.*?)\s*</frontmatter>", re.DOTALL)
_BODY_RE = re.compile(r"<body>\s*(.*?)\s*</body>", re.DOTALL)


class AttemptRecord(dict):
    """Typed alias for attempt log entries (kept as plain dict for JSON-friendliness).

    Shape: ``{"attempt": int, "ms": int, "input_tokens": int, "output_tokens": int,
    "validation_errors": list[dict] | absent}``.
    """


def parse_frontmatter_and_body(raw: str) -> tuple[dict, str]:
    """Extract `<frontmatter>` JSON and `<body>` markdown from model output."""
    fm_match = _FRONTMATTER_RE.search(raw)
    body_match = _BODY_RE.search(raw)
    if not fm_match or not body_match:
        raise ValueError(
            "response must include <frontmatter>...</frontmatter> and <body>...</body>"
        )

    try:
        fm_dict = json.loads(fm_match.group(1).strip())
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed <frontmatter> JSON: {exc.msg}") from exc

    return fm_dict, body_match.group(1).strip()


def normalize_errors(exc: Exception) -> list[dict]:
    """Normalize validation/parse failures into a uniform retry-feedback shape."""
    if isinstance(exc, ValidationError):
        return [
            {
                "loc": list(item.get("loc", [])),
                "msg": item.get("msg", "validation error"),
                "type": item.get("type", "value_error"),
            }
            for item in exc.errors()
        ]

    if isinstance(exc, json.JSONDecodeError):
        return [{"loc": ["response"], "msg": exc.msg, "type": "json_decode_error"}]

    if isinstance(exc, ValueError):
        return [{"loc": ["response"], "msg": str(exc), "type": "value_error"}]

    return [{"loc": ["response"], "msg": str(exc), "type": exc.__class__.__name__}]


def build_synth_system(prompt: Prompt, config: MarginaliaConfig) -> str:
    """Compose the synth system prompt with the wiki's purpose + style guide."""
    return (
        prompt.system
        + f"\n\n<wiki_purpose>\n{config.purpose_body}\n</wiki_purpose>"
        + f"\n\n<style_guide>\n{config.agents_body}\n</style_guide>"
    )


def _draft_fallback(analysis: SourceAnalysis, prior_errors: list[dict]) -> SourcePage:
    """Build the §7.3.1 draft fallback when retries are exhausted.

    `model_construct` is intentional: drafts must capture validation
    errors *without* themselves passing the strict cross-field
    validators that would reject (e.g.) an empty `sources` list.
    """
    return SourcePage.model_construct(
        title=analysis.proposed_title or "[unknown]",
        type=PageType.SOURCE,
        status=PageStatus.DRAFT,
        created=date.today(),
        last_synced=date.today(),
        sources=[],
        validation_errors=[json.dumps(err) for err in prior_errors],
    )


async def synthesize_page(
    analysis: SourceAnalysis,
    config: MarginaliaConfig,
    *,
    hint: str | None = None,
    existing_pages: list[dict] | None = None,
    client: Anthropic | None = None,
    prompt: Prompt | None = None,
    model: str | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    max_attempts: int = MAX_ATTEMPTS,
) -> tuple[SourcePage, list[AttemptRecord]]:
    """Synthesize a `SourcePage` from analysis with strict-schema retry.

    Returns ``(page, attempt_log)``. ``page`` may be a draft with
    ``status=draft`` and ``validation_errors[]`` populated if all
    attempts fail (design §7.3.1).
    """
    prompt = prompt or load_prompt(PROMPT_NAME)
    if client is None:
        from anthropic import Anthropic as _Anthropic

        client = _Anthropic()

    existing = existing_pages or []
    attempt_log: list[AttemptRecord] = []
    prior_errors: list[dict] = []

    schema_json = json.dumps(SourcePage.model_json_schema())
    system = build_synth_system(prompt, config)
    chosen_model = model or prompt.model or DEFAULT_MODEL

    for attempt in range(1, max_attempts + 1):
        user_msg = prompt.user_template.format(
            analysis_json=analysis.model_dump_json(),
            existing_pages_json=json.dumps(existing),
            schema_json=schema_json,
        )
        if hint:
            user_msg += f"\n\n<hint>\n{hint}\n</hint>"
        if prior_errors:
            user_msg += (
                "\n\n<validation_errors>\n"
                + json.dumps(prior_errors, indent=2)
                + "\n</validation_errors>"
            )

        t0 = time.monotonic()
        resp = client.messages.create(
            model=chosen_model,
            max_tokens=max_tokens,
            temperature=0,
            system=system,
            messages=[{"role": "user", "content": user_msg}],
        )
        record: AttemptRecord = AttemptRecord(
            attempt=attempt,
            ms=int((time.monotonic() - t0) * 1000),
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
        )
        attempt_log.append(record)

        try:
            fm_dict, _body = parse_frontmatter_and_body(resp.content[0].text)
            page = SourcePage.model_validate(fm_dict)
            return page, attempt_log
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            prior_errors = normalize_errors(exc)
            record["validation_errors"] = prior_errors

    return _draft_fallback(analysis, prior_errors), attempt_log


__all__ = [
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_MODEL",
    "MAX_ATTEMPTS",
    "PROMPT_NAME",
    "AttemptRecord",
    "build_synth_system",
    "normalize_errors",
    "parse_frontmatter_and_body",
    "synthesize_page",
]
