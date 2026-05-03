"""One-shot fixture builder for Notebook 04.

Run from the repo root:

    uv run python notebooks/_ops/build_fixtures.py

Outputs (committed via git-lfs per .gitattributes):

- notebooks/data/pdfs/clean.pdf       — text-rich 2-page PDF
- notebooks/data/pdfs/scanned.pdf     — same content rasterized (no text layer)
- notebooks/data/images/architecture.png — 4-box system diagram

Idempotent: re-running overwrites. Uses only matplotlib + pypdfium2 +
PIL — no extra dependencies. The "scanned" PDF is built by rasterizing
``clean.pdf`` and re-packing the page images with no embedded text.
"""

from __future__ import annotations

import io
from pathlib import Path

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[2]
PDF_DIR = REPO_ROOT / "notebooks" / "data" / "pdfs"
IMG_DIR = REPO_ROOT / "notebooks" / "data" / "images"


CLEAN_PDF_PAGE_1 = """
Marginalia Engine — Quick Reference

The Marginalia engine ingests source documents from a variety of
upstream systems and produces a knowledge wiki of structured pages.
The pipeline is split into two cacheable stages: analyze and
synthesize. The analyze stage extracts structured features from a
single source body using a small, deterministic prompt; the
synthesize stage composes a strict-schema page from the analysis,
retrying on validation errors up to three times before falling back
to a draft.

Source adapters dispatch by file extension. Markdown and plain text
go directly to the ingest agent. PDFs are tried as text first; if
extraction is sparse, the engine falls back to a vision pass via
Anthropic's native document content blocks. Images go through a
vision-capable model that produces a structured description.
"""

CLEAN_PDF_PAGE_2 = """
Model Selection Receipts

The engine selects models per task. Source summarization and entity
extraction run on Haiku 4.5 — high volume, narrow scope. Cross-page
synthesis and Q&A with citations run on Sonnet 4.6 — balanced cost
and reasoning. Contradiction detection, decision page drafting, and
the nightly lint pass run on Opus 4.7 — subtle reasoning and
high-stakes outputs justify the spend.

Cache discipline: every analyze call is keyed on the SHA-256 of the
source content plus the prompt version plus the engine's
CACHE_VERSION constant. Bumping a prompt without bumping the cache
version serves stale responses against new prompts — an invisible
bug. Treat CACHE_VERSION like a lockfile.
"""


def build_clean_pdf(out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with PdfPages(out) as pdf:
        for body in (CLEAN_PDF_PAGE_1, CLEAN_PDF_PAGE_2):
            fig = plt.figure(figsize=(8.5, 11))
            fig.text(
                0.10,
                0.88,
                body.split("\n\n", 1)[0].strip(),
                fontsize=18,
                weight="bold",
            )
            fig.text(
                0.10,
                0.10,
                body.split("\n\n", 1)[1].strip(),
                fontsize=11,
                wrap=True,
                verticalalignment="bottom",
            )
            pdf.savefig(fig)
            plt.close(fig)
    print(f"wrote {out.relative_to(REPO_ROOT)}  ({out.stat().st_size:,} bytes)")


def build_scanned_pdf(source: Path, out: Path) -> None:
    """Rasterize each page of `source` and re-pack with no text layer."""
    import pypdfium2

    doc = pypdfium2.PdfDocument(str(source))
    pages: list[Image.Image] = []
    for index in range(len(doc)):
        rendered = doc[index].render(scale=1.5).to_pil()
        pages.append(rendered.convert("RGB"))

    out.parent.mkdir(parents=True, exist_ok=True)
    # PIL's PDF save embeds images only — no text layer.
    pages[0].save(
        out,
        format="PDF",
        save_all=True,
        append_images=pages[1:],
        resolution=72.0,
    )
    print(f"wrote {out.relative_to(REPO_ROOT)}  ({out.stat().st_size:,} bytes)")


def build_architecture_png(out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 5)
    ax.axis("off")

    boxes = [
        (0.5, 1.5, "User\n(drops file)", "#e3f2fd"),
        (3.0, 1.5, "Adapter\n(local_fs)", "#fff3e0"),
        (5.5, 1.5, "Ingest agent\n(Haiku → Sonnet)", "#e8f5e9"),
        (8.0, 1.5, "Wiki page\n(SourcePage)", "#f3e5f5"),
    ]
    box_w, box_h = 1.6, 1.5
    centers: list[tuple[float, float]] = []
    for x, y, label, color in boxes:
        ax.add_patch(
            mpatches.FancyBboxPatch(
                (x, y),
                box_w,
                box_h,
                boxstyle="round,pad=0.05",
                ec="black",
                fc=color,
                lw=1.5,
            )
        )
        ax.text(x + box_w / 2, y + box_h / 2, label, ha="center", va="center", fontsize=11)
        centers.append((x + box_w, y + box_h / 2))

    for i in range(len(boxes) - 1):
        x_end = boxes[i + 1][0]
        y = centers[i][1]
        ax.annotate(
            "",
            xy=(x_end, y),
            xytext=(centers[i][0], y),
            arrowprops=dict(arrowstyle="->", lw=1.6, color="black"),
        )

    ax.text(5, 4.3, "Marginalia ingest pipeline", ha="center", fontsize=14, weight="bold")
    ax.text(5, 0.5, "design §8 (adapter dispatch) → §7.1 (ingest agent)",
            ha="center", fontsize=9, style="italic", color="#555")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    out.write_bytes(buf.getvalue())
    print(f"wrote {out.relative_to(REPO_ROOT)}  ({out.stat().st_size:,} bytes)")


def main() -> None:
    clean_pdf = PDF_DIR / "clean.pdf"
    scanned_pdf = PDF_DIR / "scanned.pdf"
    arch_png = IMG_DIR / "architecture.png"

    build_clean_pdf(clean_pdf)
    build_scanned_pdf(clean_pdf, scanned_pdf)
    build_architecture_png(arch_png)


if __name__ == "__main__":
    main()
