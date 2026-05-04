"""Activity log generator — readable view of recent ingest history.

The audit DB has ``ingest_history`` (one row per ingest) but no
human-readable surface. This module renders the last N rows as a
markdown list suitable for ``marginalia log`` (stdout) or
``<wiki>/log.md`` (committed snapshot).

Generated, never appended: every call rebuilds the body from
``audit.db``, so there's no race with the worker writing rows. If
the user hand-edits ``log.md``, the next ``--write`` overwrites
their edits — that's intentional, since the log is regenerable
state, not an authored document.
"""

from __future__ import annotations

from pathlib import Path

from engine.audit.queries import last_n_ingests

LOG_FILENAME = "log.md"
DEFAULT_LIMIT = 20


def format_activity_log(audit_db_path: Path, *, limit: int = DEFAULT_LIMIT) -> str:
    """Render the last N ingests from ``audit.db`` as markdown.

    Each row becomes a list item with timestamp, source, page paths,
    and cost. Empty DB → emits a placeholder so callers never get an
    empty string and can show the message verbatim.

    Output shape::

        # Activity Log

        _Last 20 ingests from audit.db. Generated: 2026-05-04T14:22:03+00:00_

        - **2026-05-04T14:22:03** ingested `https://example.com/article`
          → `knowledge/concepts/example.md` ($0.0123, 1.4s)
        - **2026-05-04T14:18:11** ingested `~/wiki-raw/notes.md`
          → `knowledge/concepts/notes.md` ($0.0089, 0.8s)
    """
    header_title = "# Activity Log"
    if not audit_db_path.is_file():
        return f"{header_title}\n\n_(audit.db not initialised yet)_\n"

    rows = last_n_ingests(audit_db_path, limit=limit)
    if not rows:
        return f"{header_title}\n\n_No ingests recorded yet._\n"

    lines: list[str] = [
        header_title,
        "",
        f"_Last {len(rows)} ingest(s) from `audit.db`._",
        "",
    ]
    for row in rows:
        lines.append(_format_row(row))
    lines.append("")
    return "\n".join(lines)


def _format_row(row: dict) -> str:
    """One ingest row → one markdown bullet (potentially with continuation indent)."""
    ts = row["timestamp"]
    source_ref = row["source_ref"]
    paths = row["page_paths"]
    cost = row["cost_usd"]
    duration_ms = row["duration_ms"]
    duration_s = duration_ms / 1000.0

    if not paths:
        page_str = "_(no pages written — likely a draft or failure)_"
    elif len(paths) == 1:
        page_str = f"→ `{paths[0]}`"
    else:
        page_str = f"→ {', '.join(f'`{p}`' for p in paths)}"

    return f"- **{ts}** ingested `{source_ref}`\n  {page_str} (${cost:.4f}, {duration_s:.1f}s)"


def write_activity_log(
    wiki_root: Path,
    *,
    audit_db_path: Path | None = None,
    limit: int = DEFAULT_LIMIT,
) -> Path:
    """Render and write to ``<wiki_root>/log.md``. Returns the path."""
    audit_db_path = audit_db_path or wiki_root / ".wiki" / "audit.db"
    body = format_activity_log(audit_db_path, limit=limit)
    out = wiki_root / LOG_FILENAME
    out.write_text(body, encoding="utf-8")
    return out


__all__ = [
    "DEFAULT_LIMIT",
    "LOG_FILENAME",
    "format_activity_log",
    "write_activity_log",
]
