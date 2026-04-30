"""Wiki-level configuration loader (design.md §6.4).

`purpose.md` declares the wiki's *scope* — strategic, CTO-level review.
`AGENTS.md` declares the wiki's *style and terminology* — editorial,
curator-level review.

Both files are markdown with no fixed schema. The agent consumes them
as prose: this module's job is to load them, not to parse them. The
`in_scope` / `out_of_scope` lists are best-effort extraction for CLI
display — agents always receive `purpose_body` verbatim.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

PURPOSE_FILENAME = "purpose.md"
AGENTS_FILENAME = "AGENTS.md"

_BULLET_RE = re.compile(r"^\s*-\s+(.+?)\s*$")
_IN_SCOPE_HEADER_RE = re.compile(r"^#+\s*(?:sources?\s+)?in\s+scope", re.IGNORECASE)
_OUT_OF_SCOPE_HEADER_RE = re.compile(r"^#+\s*out\s+of\s+scope", re.IGNORECASE)
_INLINE_IN_SCOPE_RE = re.compile(r"in\s+scope\s*:\s*$", re.IGNORECASE)
_INLINE_OUT_OF_SCOPE_RE = re.compile(r"out\s+of\s+scope[^:]*:\s*$", re.IGNORECASE)


class WikiConfigError(RuntimeError):
    """Raised when required wiki-config files are missing or unreadable."""


class MarginaliaConfig(BaseModel):
    """Loaded `purpose.md` + `AGENTS.md` for one wiki.

    The agent receives `purpose_body` and `agents_body` as system-prompt
    material (NB 02 onwards). `in_scope` / `out_of_scope` are extracted
    for display and tooling only — they are *not* authoritative. Always
    pass the raw bodies to the agent.
    """

    model_config = ConfigDict(extra="forbid")

    wiki_root: Path
    purpose_path: Path
    agents_path: Path
    purpose_body: str
    agents_body: str
    in_scope: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)

    @classmethod
    def load(cls, wiki_root: Path | str) -> MarginaliaConfig:
        """Read both files relative to `wiki_root`. Raises if either is missing."""
        root = Path(wiki_root)
        purpose_path = root / PURPOSE_FILENAME
        agents_path = root / AGENTS_FILENAME

        missing = [p for p in (purpose_path, agents_path) if not p.is_file()]
        if missing:
            names = ", ".join(p.name for p in missing)
            raise WikiConfigError(
                f"missing required wiki-config file(s): {names} (looked under {root})"
            )

        purpose_body = purpose_path.read_text(encoding="utf-8")
        agents_body = agents_path.read_text(encoding="utf-8")
        in_scope, out_of_scope = _extract_scope_bullets(purpose_body)

        return cls(
            wiki_root=root,
            purpose_path=purpose_path,
            agents_path=agents_path,
            purpose_body=purpose_body,
            agents_body=agents_body,
            in_scope=in_scope,
            out_of_scope=out_of_scope,
        )


def _extract_scope_bullets(purpose_body: str) -> tuple[list[str], list[str]]:
    """Heuristic extraction of bullet lists under in-scope / out-of-scope sections.

    Recognises both header form (`## Sources in scope`) and inline form
    (`Sources in scope:` followed by bullets). Bullets are collected
    until the next header or a blank line followed by non-bullet text.
    """
    in_scope: list[str] = []
    out_of_scope: list[str] = []
    current: list[str] | None = None

    lines = purpose_body.splitlines()
    for raw in lines:
        line = raw.rstrip()

        if _IN_SCOPE_HEADER_RE.match(line) or _INLINE_IN_SCOPE_RE.search(line):
            current = in_scope
            continue
        if _OUT_OF_SCOPE_HEADER_RE.match(line) or _INLINE_OUT_OF_SCOPE_RE.search(line):
            current = out_of_scope
            continue
        if line.startswith("#"):
            current = None
            continue

        if current is None:
            continue

        bullet = _BULLET_RE.match(line)
        if bullet:
            current.append(bullet.group(1))
        elif line.strip() == "":
            continue
        else:
            current = None

    return in_scope, out_of_scope
