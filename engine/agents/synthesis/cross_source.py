"""Cross-source synthesis (design §7.1, §10A step 5).

Reads N existing ``SourcePage`` objects and produces ONE new page —
typically a ``ConceptPage``, optionally an ``AnalysisPage`` or
``DecisionPage`` — that cites all of them via ``[[wikilink]]``
references.

Two retry classes folded into the same loop:

1. **Pydantic / parse failures** — handled by the shared ingest
   helpers (``parse_frontmatter_and_body``, ``normalize_errors``).
2. **Citation failures** — dangling ``[[paths]]`` that don't resolve
   to anything in ``<available_paths>``, or input sources that the
   page didn't bother to cite. Returned as validation-error-shaped
   dicts so the same retry feedback channel works.

After ``MAX_ATTEMPTS`` failures the page lands as ``status=draft``
with ``validation_errors[]`` populated — same §7.3.1 contract as
ingest synthesize. Never a silent commit.
"""

from __future__ import annotations

import json
import re
import time
from datetime import date
from typing import TYPE_CHECKING

from pydantic import ValidationError

from engine.agents.ingest.synthesize import (
    AttemptRecord,
    normalize_errors,
    parse_frontmatter_and_body,
)
from engine.models.pages import (
    AnalysisPage,
    ConceptPage,
    Confidence,
    DecisionPage,
    Page,
    PageStatus,
    PageType,
    SourcePage,
)
from engine.prompts import Prompt, load_prompt
from engine.utils.api_compat import temperature_kwargs

if TYPE_CHECKING:
    from anthropic import Anthropic

    from engine.models.wiki_config import MarginaliaConfig

PROMPT_NAME = "synthesis"
DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_MAX_TOKENS = 4096
MAX_ATTEMPTS = 3

_TARGET_CLASSES: dict[PageType, type[Page]] = {
    PageType.CONCEPT: ConceptPage,
    PageType.ANALYSIS: AnalysisPage,
    PageType.DECISION: DecisionPage,
}

_WIKILINK_INNER_RE = re.compile(r"\[\[([^\[\]]+)\]\]")
_SLUG_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_SLUG_LEADING_NON_ALNUM_RE = re.compile(r"^[^a-z0-9]+")


def extract_wikilinks(text: str) -> list[str]:
    """Return inner paths of every ``[[path]]`` reference in ``text``."""
    return _WIKILINK_INNER_RE.findall(text)


def derive_default_path(page: SourcePage) -> str:
    """Slugify ``page.title`` into a wikilink-safe path under ``sources/``.

    The output matches the WIKILINK_RE inner constraint
    (``[a-z0-9][a-z0-9/_-]*``) so it can be wrapped as
    ``[[sources/<slug>]]``.
    """
    title = page.title.strip().lower()
    slug = _SLUG_NON_ALNUM_RE.sub("-", title).strip("-")
    slug = _SLUG_LEADING_NON_ALNUM_RE.sub("", slug)
    if not slug:
        slug = "untitled"
    return f"sources/{slug}"


def _frontmatter_wikilinks(fm_dict: dict) -> set[str]:
    """Return the inner-path set of wikilinks in frontmatter relational fields."""
    found: set[str] = set()
    for field in ("related", "contradicts"):
        for value in fm_dict.get(field) or []:
            if isinstance(value, str):
                found.update(extract_wikilinks(value))
    supersedes = fm_dict.get("supersedes")
    if isinstance(supersedes, str):
        found.update(extract_wikilinks(supersedes))
    return found


def verify_citations(
    page: Page,
    body: str,
    available_paths: set[str],
    *,
    require_all_sources_cited: bool = True,
    sources_paths: list[str] | None = None,
) -> list[dict]:
    """Return validation-error-shaped dicts for citation failures.

    Two checks run in order. Each error dict carries a ``type`` field
    so callers can distinguish from Pydantic errors:

    - ``"dangling_citation"`` — a wikilink in body or frontmatter that
      doesn't resolve to any path in ``available_paths``.
    - ``"missing_source_citation"`` — when ``require_all_sources_cited``
      is on, a source path from ``sources_paths`` that the page failed
      to cite anywhere (frontmatter relational fields, body, or the
      ``sources`` ref list).

    Empty list = clean.
    """
    errors: list[dict] = []
    fm_dict = page.model_dump(mode="json")

    cited_in_fm = _frontmatter_wikilinks(fm_dict)
    cited_in_body = set(extract_wikilinks(body))
    all_cited = cited_in_fm | cited_in_body

    dangling = sorted(all_cited - available_paths)
    for path in dangling:
        errors.append(
            {
                "loc": ["citations", path],
                "msg": f"wikilink [[{path}]] does not resolve to a known wiki path",
                "type": "dangling_citation",
            }
        )

    if require_all_sources_cited and sources_paths:
        missing = [p for p in sources_paths if p not in all_cited]
        for path in missing:
            errors.append(
                {
                    "loc": ["citations", "missing", path],
                    "msg": (
                        f"input source path [[{path}]] must be cited in body "
                        "or frontmatter `related`"
                    ),
                    "type": "missing_source_citation",
                }
            )

    return errors


def _draft_fallback(
    target_class: type[Page],
    target_type: PageType,
    sources: list[SourcePage],
    prior_errors: list[dict],
) -> Page:
    """Build a draft when all retry attempts fail (§7.3.1)."""
    today = date.today()
    base_kwargs: dict = {
        "title": sources[0].title if sources else "[unknown]",
        "type": target_type,
        "status": PageStatus.DRAFT,
        "created": today,
        "last_synced": today,
        "validation_errors": [json.dumps(err) for err in prior_errors],
    }
    if target_class is AnalysisPage:
        base_kwargs["sources"] = []
        base_kwargs["confidence"] = Confidence.LOW
    elif target_class is ConceptPage:
        base_kwargs["confidence"] = Confidence.LOW
    elif target_class is DecisionPage:
        base_kwargs["owners"] = []
        base_kwargs["confidence"] = Confidence.LOW
    return target_class.model_construct(**base_kwargs)


async def synthesize_cross_source(
    sources: list[SourcePage],
    config: MarginaliaConfig,
    *,
    target_type: PageType = PageType.CONCEPT,
    hint: str | None = None,
    existing_pages: list[Page] | None = None,
    sources_paths: list[str] | None = None,
    client: Anthropic | None = None,
    prompt: Prompt | None = None,
    model: str | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    max_attempts: int = MAX_ATTEMPTS,
    require_all_sources_cited: bool = True,
) -> tuple[Page, str, list[AttemptRecord]]:
    """Synthesize one new page from N source pages.

    Returns ``(page, body, attempt_log)``. ``page`` may be a draft
    with ``status=draft`` and ``validation_errors[]`` populated if
    every attempt failed schema or citation validation.
    """
    if target_type not in _TARGET_CLASSES:
        raise ValueError(
            f"target_type must be one of {sorted(t.value for t in _TARGET_CLASSES)}; got {target_type!r}"
        )
    target_class = _TARGET_CLASSES[target_type]

    prompt = prompt or load_prompt(PROMPT_NAME)
    if client is None:
        from anthropic import Anthropic as _Anthropic

        client = _Anthropic()

    paths = (
        list(sources_paths)
        if sources_paths is not None
        else [derive_default_path(s) for s in sources]
    )
    if len(paths) != len(sources):
        raise ValueError(
            f"sources_paths length ({len(paths)}) must match sources length ({len(sources)})"
        )
    available = set(paths)

    target_schema_json = json.dumps(target_class.model_json_schema())
    new_sources_json = json.dumps(
        [{**s.model_dump(mode="json"), "_path": paths[i]} for i, s in enumerate(sources)],
        indent=2,
    )
    existing_pages_json = json.dumps([p.model_dump(mode="json") for p in (existing_pages or [])])
    available_paths_block = "\n".join(f"- {p}" for p in sorted(available))

    chosen_model = model or prompt.model or DEFAULT_MODEL
    system = (
        prompt.system
        + f"\n\n<wiki_purpose>\n{config.purpose_body}\n</wiki_purpose>"
        + f"\n\n<style_guide>\n{config.agents_body}\n</style_guide>"
    )

    attempt_log: list[AttemptRecord] = []
    prior_errors: list[dict] = []

    for attempt in range(1, max_attempts + 1):
        user_msg = prompt.user_template.format(
            existing_pages_json=existing_pages_json,
            new_sources_json=new_sources_json,
            available_paths_block=available_paths_block,
            target_schema_json=target_schema_json,
            target_type=target_type.value,
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
            **temperature_kwargs(chosen_model),
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
            fm_dict, body = parse_frontmatter_and_body(resp.content[0].text)
            page = target_class.model_validate(fm_dict)
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            prior_errors = normalize_errors(exc)
            record["validation_errors"] = prior_errors
            continue

        citation_errors = verify_citations(
            page,
            body,
            available,
            require_all_sources_cited=require_all_sources_cited,
            sources_paths=paths,
        )
        if citation_errors:
            prior_errors = citation_errors
            record["validation_errors"] = citation_errors
            continue

        return page, body, attempt_log

    return _draft_fallback(target_class, target_type, sources, prior_errors), "", attempt_log


__all__ = [
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_MODEL",
    "MAX_ATTEMPTS",
    "PROMPT_NAME",
    "derive_default_path",
    "extract_wikilinks",
    "synthesize_cross_source",
    "verify_citations",
]
