"""Prompt loader for engine agent prompts.

Prompts live as markdown files alongside this module: one file per
(agent, role) pair. Each file carries YAML frontmatter with at least a
`version` field, and a body split into `## System` and
`## User Template` sections. The version travels with the file so
`CACHE_VERSION` discipline (design §7.6) has a single place to point at.
"""

from __future__ import annotations

import re
from pathlib import Path

import frontmatter
from pydantic import BaseModel, ConfigDict

PROMPTS_DIR = Path(__file__).parent

_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_SYSTEM_HEADING = "system"
_USER_TEMPLATE_HEADING = "user template"


class PromptLoadError(RuntimeError):
    """Raised when a prompt file is missing required structure."""


class Prompt(BaseModel):
    """A loaded prompt file: metadata + system + user template strings."""

    model_config = ConfigDict(extra="forbid")

    name: str
    version: str
    role: str | None = None
    model: str | None = None
    system: str
    user_template: str


def load_prompt(name: str, *, prompts_dir: Path | None = None) -> Prompt:
    """Load `<prompts_dir>/<name>.md` and parse it into a `Prompt`.

    The file must declare `version` in its frontmatter and contain
    both `## System` and `## User Template` sections.
    """
    base = prompts_dir or PROMPTS_DIR
    path = base / f"{name}.md"
    if not path.is_file():
        raise PromptLoadError(f"prompt file not found: {path}")

    post = frontmatter.loads(path.read_text(encoding="utf-8"))
    meta = dict(post.metadata)
    version = meta.get("version")
    if not version:
        raise PromptLoadError(f"prompt {path.name} missing required `version` frontmatter")

    sections = _split_sections(post.content)
    try:
        system = sections[_SYSTEM_HEADING]
        user_template = sections[_USER_TEMPLATE_HEADING]
    except KeyError as exc:
        raise PromptLoadError(
            f"prompt {path.name} must contain `## System` and `## User Template` sections"
        ) from exc

    return Prompt(
        name=str(meta.get("name", name)),
        version=str(version),
        role=meta.get("role"),
        model=meta.get("model"),
        system=system,
        user_template=user_template,
    )


def _split_sections(body: str) -> dict[str, str]:
    """Split a markdown body on `## ` headings into a {heading_lower: text} map."""
    matches = list(_SECTION_RE.finditer(body))
    if not matches:
        return {}

    out: dict[str, str] = {}
    for i, match in enumerate(matches):
        heading = match.group(1).strip().lower()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        out[heading] = body[start:end].strip("\n")
    return out


__all__ = ["Prompt", "PromptLoadError", "load_prompt", "PROMPTS_DIR"]
