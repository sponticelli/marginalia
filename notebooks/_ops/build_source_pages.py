"""One-shot SourcePage fixture builder for Notebook 05.

Run from the repo root:

    uv run python notebooks/_ops/build_source_pages.py

Outputs:

- notebooks/data/poc-wiki/sources/apollo-launch-sync.md
- notebooks/data/poc-wiki/sources/marginalia-engine-quick-reference.md
- notebooks/data/poc-wiki/sources/marginalia-pipeline-architecture.md

Each is a real ``SourcePage`` markdown file with frontmatter +
body, produced by running the ingest pipeline on the three
representative ``raw/`` fixtures (one per extraction method:
text, PDF document block, image vision). Idempotent: re-running
overwrites the outputs.

The hand-curated contradictory pair (apollo-q2.md, apollo-q3.md)
is NOT generated here — those are committed by hand because they
need deliberately contradictory content for NB 05's edge case.

Cost: roughly $0.05 per run (1 PDF document block + 1 image
vision + 3 ingest synthesizes on Sonnet 4.6).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import frontmatter
from anthropic import Anthropic
from dotenv import load_dotenv

from engine.adapters.local_fs.image import extract_image
from engine.adapters.local_fs.pdf import extract_pdf
from engine.agents.ingest import analyze_source, synthesize_page
from engine.agents.synthesis.cross_source import derive_default_path
from engine.models.pages import SourceKind
from engine.models.wiki_config import MarginaliaConfig

REPO = Path(__file__).resolve().parents[2]
RAW = REPO / "notebooks" / "data" / "poc-wiki" / "raw"
SOURCES = REPO / "notebooks" / "data" / "poc-wiki" / "sources"


async def _ingest(path: Path, config: MarginaliaConfig, client: Anthropic) -> tuple[str, str]:
    """Run the right adapter + analyze + synthesize; return (frontmatter dict, body)."""
    if path.suffix == ".md":
        text = path.read_text(encoding="utf-8")
    elif path.suffix == ".pdf":
        text = extract_pdf(path, client=client).text
    elif path.suffix in {".png", ".jpg", ".jpeg"}:
        text = extract_image(path, client=client).text
    else:
        raise ValueError(f"unsupported fixture extension: {path.suffix}")

    analysis = await analyze_source(text, SourceKind.LOCAL_FILE, config, client=client)
    page, body, _log = await synthesize_page(analysis, config, client=client)
    return page.model_dump(mode="json", exclude_none=True), body


def _slug_path_for(fm_dict: dict) -> Path:
    from engine.models.pages import SourcePage

    page = SourcePage.model_validate(fm_dict)
    slug = derive_default_path(page).removeprefix("sources/")
    return SOURCES / f"{slug}.md"


async def main() -> None:
    load_dotenv()
    SOURCES.mkdir(parents=True, exist_ok=True)
    config = MarginaliaConfig.load(REPO / "notebooks" / "data" / "poc-wiki")
    client = Anthropic()

    inputs = [
        RAW / "good_source.md",
        RAW / "clean.pdf",
        RAW / "architecture.png",
    ]

    for input_path in inputs:
        fm_dict, body = await _ingest(input_path, config, client)
        out = _slug_path_for(fm_dict)
        post = frontmatter.Post(content=body, **fm_dict)
        out.write_text(frontmatter.dumps(post), encoding="utf-8")
        print(f"wrote {out.relative_to(REPO)}  ({out.stat().st_size:,} bytes)  ← {input_path.name}")


if __name__ == "__main__":
    asyncio.run(main())
