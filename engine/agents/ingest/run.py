"""End-to-end ingest chain (extract → analyze → synthesize → upsert).

The full §7.1 ingest pipeline as a single callable. Originally lived as
``_run_ingest_subagent`` inside the orchestrator's tool loop; promoted
here so the §7.5 job worker can dispatch ``ingest`` jobs through the
same code path the orchestrator uses, with no duplication.

The function is idempotent at the page level — ``upsert_page`` writes
to ``wiki_root/sources/<path>.md`` whether or not the file already
exists. Re-running the same ``user_input`` against the same wiki
overwrites the page with whatever the latest analyze + synthesize step
produces (subject to L1 cache hits for the analyze step).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from anthropic import Anthropic

    from engine.utils.cost_tracker import CostRecord


async def run_ingest_chain(
    user_input: str,
    *,
    config,
    client: Anthropic,
    wiki_root: Path,
    hint: str | None = None,
    on_cost: Callable[[CostRecord], None] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Run extract → analyze → synthesize → upsert for one source.

    Returns ``(summary, result)`` where ``summary`` is a one-line
    human-readable string and ``result`` is a dict suitable for
    serializing into the job queue's ``result`` column.

    The result dict carries observability hooks the dispatcher uses
    to write the §13.2 ``ingest_history`` row:
    - ``path``      — wiki page path (also in summary)
    - ``status``    — ``page.status``
    - ``title``     — ``page.title``
    - ``content_sha256`` — analyzer's source hash (cache-key spine)
    - ``confidence``     — page-level confidence (None when not set)

    ``on_cost`` is forwarded to both ``analyze_source`` and
    ``synthesize_page``; per-attempt records arrive on every API call.

    On extraction failure (e.g. unsupported file, fetch error), the
    function returns early with the failure reason in both the summary
    and the result — no page is written, no cost callbacks fire.
    """
    from engine.agents.ingest.analyze import analyze_source
    from engine.agents.ingest.synthesize import synthesize_page
    from engine.agents.synthesis.cross_source import derive_default_path
    from engine.models.pages import SourceKind
    from engine.tools.upsert_page import upsert_page
    from engine.utils.dispatch import (
        Adapter,
        extract,
        extract_url,
        route_path,
        route_url,
    )

    is_url = user_input.startswith(("http://", "https://"))
    # Decide the adapter *before* doing the work so we can stamp it onto
    # SourceRefs after synthesis. This is what `marginalia resync` reads
    # to pick the same adapter again deterministically.
    adapter: Adapter
    if is_url:
        adapter = route_url(user_input)
        extracted = await extract_url(user_input, client=client)
    else:
        adapter = route_path(user_input)
        extracted = extract(Path(user_input), client=client)

    if extracted.failure_reason:
        return f"extraction failed: {extracted.failure_reason}", {
            "extraction_method": extracted.extraction_method,
            "adapter": adapter,
            "failure_reason": extracted.failure_reason,
        }

    source_kind = SourceKind.YOUTUBE_VIDEO if is_url else SourceKind.LOCAL_FILE
    analysis = await analyze_source(
        extracted.text, source_kind, config, client=client, on_cost=on_cost
    )
    page, body, _log = await synthesize_page(
        analysis, config, hint=hint, client=client, on_cost=on_cost
    )

    # Stamp the adapter onto every SourceRef whose `ref` matches the
    # input we just processed. The LLM doesn't know about adapters; we
    # patch them in here so resync has a deterministic routing key.
    for source in page.sources:
        if source.ref == user_input and source.adapter is None:
            source.adapter = adapter

    page_path = derive_default_path(page)

    upsert_page(
        page_path,
        page.model_dump(mode="json", exclude_none=True),
        body,
        wiki_root=wiki_root,
    )
    summary = f"ingested {user_input!r} → {page_path} ({page.status.value})"
    return summary, {
        "path": page_path,
        "status": page.status.value,
        "title": page.title,
        "content_sha256": analysis.content_sha256,
        "confidence": page.confidence.value if page.confidence else None,
        "adapter": adapter,
    }


__all__ = ["run_ingest_chain"]
