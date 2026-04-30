"""Page schemas for the Marginalia wiki.

The Pydantic models defined here are the source of truth.
JSON Schemas exported via `model_json_schema()` are consumed by the *content* repo (see `_ops/schemas/`) for PR-time validation in CI.

Cross-field invariants (e.g. archived pages must carry archive metadata) are enforced here in Pydantic and will *not* round-trip through the exported JSON Schema — that's expected.
Wiki-side CI catches structural errors; the engine catches semantic ones.
"""

from __future__ import annotations

import re
from datetime import date
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

WIKILINK_RE = re.compile(r"^\[\[[a-z0-9][a-z0-9/_-]*\]\]$")


class PageType(StrEnum):
    ENTITY = "entity"
    CONCEPT = "concept"
    SOURCE = "source"
    DECISION = "decision"
    MEETING = "meeting"
    METRIC = "metric"
    QA = "qa"
    ANALYSIS = "analysis"


class PageStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    STALE = "stale"
    ARCHIVED = "archived"
    SUPERSEDED = "superseded"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class SourceAuthority(StrEnum):
    CANONICAL = "canonical"
    CORROBORATING = "corroborating"
    OFFHAND = "offhand"


class SourceKind(StrEnum):
    NOTION_PAGE = "notion_page"
    SLACK_THREAD = "slack_thread"
    HEX_DASHBOARD = "hex_dashboard"
    GRANOLA_MEETING = "granola_meeting"
    GDRIVE_FILE = "gdrive_file"
    YOUTUBE_VIDEO = "youtube_video"
    WEB_PAGE = "web_page"
    LOCAL_FILE = "local_file"


class ArchivedReason(StrEnum):
    UPSTREAM_DELETED = "upstream-deleted"
    SUPERSEDED = "superseded"
    MANUAL = "manual"


class SourceRef(BaseModel):
    """One row in the `sources:` frontmatter array (design §6.1)."""

    model_config = ConfigDict(extra="forbid")

    ref: str
    kind: SourceKind
    captured: date
    authority: SourceAuthority


def _validate_wikilinks(values: list[str]) -> list[str]:
    bad = [v for v in values if not WIKILINK_RE.match(v)]
    if bad:
        raise ValueError(
            f"wikilinks must match `[[namespaced/path]]` (got {bad!r}); "
            "see design.md §6.3 — bare names like `Apollo` are not allowed"
        )
    return values


class BasePage(BaseModel):
    """Universal frontmatter (design §6.1).

    Subclasses pin `type` to a specific `PageType` and tighten required
    fields per the §6.2 contract table. Use `Page` (the discriminated
    union below) when you need polymorphic validation.
    """

    model_config = ConfigDict(extra="forbid", use_enum_values=False)

    title: str = Field(min_length=1)
    type: PageType
    status: PageStatus = PageStatus.DRAFT
    created: date
    last_synced: date
    confidence: Confidence | None = None
    sources: list[SourceRef] = Field(default_factory=list)
    owners: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    related: list[str] = Field(default_factory=list)
    supersedes: str | None = None
    contradicts: list[str] = Field(default_factory=list)

    archived_date: date | None = None
    archived_reason: ArchivedReason | None = None
    validation_errors: list[str] = Field(default_factory=list)

    @field_validator("owners", "related", "contradicts")
    @classmethod
    def _check_wikilink_lists(cls, v: list[str]) -> list[str]:
        return _validate_wikilinks(v)

    @field_validator("supersedes")
    @classmethod
    def _check_supersedes(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return _validate_wikilinks([v])[0]

    @model_validator(mode="after")
    def _check_archive_consistency(self) -> BasePage:
        archived = self.status == PageStatus.ARCHIVED
        if archived and (self.archived_date is None or self.archived_reason is None):
            raise ValueError(
                "archived pages require both `archived_date` and `archived_reason` "
                "(design §6.1, §10 Scenario H)"
            )
        if not archived and (self.archived_date is not None or self.archived_reason is not None):
            raise ValueError(
                "`archived_date` / `archived_reason` may only be set when status='archived'"
            )
        return self

    @model_validator(mode="after")
    def _check_validation_errors_only_on_drafts(self) -> BasePage:
        if self.validation_errors and self.status != PageStatus.DRAFT:
            raise ValueError(
                "`validation_errors` may only be populated on draft pages "
                "(design §7.3.1: degraded ingest lands as status=draft)"
            )
        return self


class EntityPage(BasePage):
    """People, projects, customers, teams (design §6.2 row 1)."""

    type: Literal[PageType.ENTITY] = PageType.ENTITY
    tags: list[str] = Field(min_length=1)
    owners: list[str] = Field(min_length=1)


class ConceptPage(BasePage):
    """Glossary-style definitions (design §6.2 row 2)."""

    type: Literal[PageType.CONCEPT] = PageType.CONCEPT
    confidence: Confidence


class SourcePage(BasePage):
    """A summary page for an external source (design §6.2 row 3)."""

    type: Literal[PageType.SOURCE] = PageType.SOURCE
    sources: list[SourceRef] = Field(min_length=1)


class DecisionPage(BasePage):
    """Canonical decisions (design §6.2 row 4)."""

    type: Literal[PageType.DECISION] = PageType.DECISION
    owners: list[str] = Field(min_length=1)
    confidence: Confidence


class MeetingPage(BasePage):
    """Meeting notes; owners are attendees (design §6.2 row 5)."""

    type: Literal[PageType.MEETING] = PageType.MEETING
    sources: list[SourceRef] = Field(min_length=1)
    owners: list[str] = Field(min_length=1)


class MetricPage(BasePage):
    """A KPI / dashboard-backed metric (design §6.2 row 6)."""

    type: Literal[PageType.METRIC] = PageType.METRIC
    owners: list[str] = Field(min_length=1)


class QAPage(BasePage):
    """Filed Q&A — questions become permanent assets (design §6.2 row 7)."""

    type: Literal[PageType.QA] = PageType.QA
    sources: list[SourceRef] = Field(min_length=1)


class AnalysisPage(BasePage):
    """Investigations and write-ups (design §6.2 row 8)."""

    type: Literal[PageType.ANALYSIS] = PageType.ANALYSIS
    confidence: Confidence
    sources: list[SourceRef] = Field(min_length=1)


"""Discriminated union over every page type.

Use this when validating arbitrary frontmatter:

    from pydantic import TypeAdapter
    page = TypeAdapter(Page).validate_python(frontmatter_dict)

Pydantic dispatches on the `type` field and returns the appropriate
subclass. A wrong or missing `type` produces a single targeted error
instead of eight separate ones.
"""
Page = Annotated[
    EntityPage
    | ConceptPage
    | SourcePage
    | DecisionPage
    | MeetingPage
    | MetricPage
    | QAPage
    | AnalysisPage,
    Field(discriminator="type"),
]
