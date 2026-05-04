"""Generator for ``<wiki>/dashboard.md`` (design §13.3).

The dashboard mixes two render strategies in one file:

1. **Dataview blocks** for sections sourced from page frontmatter
   (orphans, contradictions, stale, health). Obsidian's Dataview
   plugin renders these live when the user opens the file.
2. **Server-rendered markdown table** for the cost section, fed from
   ``audit.db`` — Dataview can't query SQLite, so we materialise the
   result at generation time.

Re-running ``generate_dashboard`` produces a fresh body from the
current wiki + audit-DB state. Safe to commit the output to the wiki
repo: it's a static markdown snapshot of the live dashboard.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from engine.audit.queries import daily_cost_breakdown

DATAVIEW_BLOCKS = """\
## Health (active decisions)

```dataview
TABLE
  length(file.inlinks) as "Inbound",
  status,
  confidence
FROM ""
WHERE type = "decision" AND status = "active"
SORT file.mtime DESC
LIMIT 10
```

## Orphans (no inbound links)

```dataview
LIST FROM ""
WHERE length(file.inlinks) = 0 AND type != "source"
```

## Contradictions

```dataview
TABLE contradicts
FROM ""
WHERE contradicts != null
```

## Stale (last_synced > 14 days)

```dataview
TABLE last_synced
FROM ""
WHERE date(today) - date(last_synced) > dur(14 days)
SORT last_synced ASC
```
"""


def _format_cost_section(audit_db_path: Path, *, days: int = 7) -> str:
    """Render a markdown table of per-day cost from ``audit.db``.

    Empty DB → emits a placeholder so the section is never blank.
    """
    if not audit_db_path.is_file():
        return f"## Cost — last {days} days\n\n" "_(audit.db not initialised yet)_\n"

    rows = daily_cost_breakdown(audit_db_path, days=days)
    if not rows:
        return f"## Cost — last {days} days\n\n" "_No LLM calls recorded in this window._\n"

    lines = [f"## Cost — last {days} days", "", "| Day | Calls | Cost (USD) |", "|---|---|---|"]
    total = 0.0
    for row in rows:
        lines.append(f"| {row['day']} | {row['calls']} | ${row['cost_usd']:.4f} |")
        total += row["cost_usd"]
    lines.append(f"| **Total** | — | **${total:.4f}** |")
    lines.append("")
    return "\n".join(lines)


def generate_dashboard(
    wiki_root: Path,
    *,
    audit_db_path: Path | None = None,
    days: int = 7,
    now: datetime | None = None,
) -> str:
    """Build a fresh ``dashboard.md`` body from current wiki + audit state.

    ``wiki_root`` is reserved for future template overrides (e.g. a
    wiki-specific dashboard template). ``audit_db_path`` defaults to
    ``<wiki_root>/.wiki/audit.db`` if omitted.

    Output: the full body, ready to be written to
    ``<wiki_root>/dashboard.md``. Frontmatter is intentionally absent —
    Obsidian Dataview reads the file as a regular note.
    """
    audit_db_path = audit_db_path or wiki_root / ".wiki" / "audit.db"
    now = now or datetime.now(UTC)

    header = (
        "# Wiki Dashboard\n\n"
        f"_Generated: {now.isoformat(timespec='seconds')}_\n\n"
        "Live health view rendered by Obsidian's Dataview plugin. The Cost\n"
        "section below is a server-rendered snapshot from `audit.db` —\n"
        "re-run `engine.audit.dashboard.generate_dashboard` to refresh it.\n"
    )
    cost_section = _format_cost_section(audit_db_path, days=days)
    return f"{header}\n{DATAVIEW_BLOCKS}\n{cost_section}"


__all__ = ["DATAVIEW_BLOCKS", "generate_dashboard"]
