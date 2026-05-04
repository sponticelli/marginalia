"""Atomic backlink rewriter for the §10 Scenario H archive flow.

When a page moves from ``knowledge/decisions/X.md`` to
``knowledge/decisions/archived/X.md``, every other page that referenced
the old path — in body ``[[wikilinks]]`` or in ``related[]`` /
``contradicts[]`` / ``supersedes`` frontmatter — must be rewritten in
the same operation. Otherwise the wiki rots silently.

The pattern is **plan-then-apply**:

1. ``compute_archive_patchset`` walks the wiki, builds a complete
   patchset in memory, and re-validates every patched page against its
   Pydantic schema. *No disk write happens here.* Validation failures
   raise ``pydantic.ValidationError`` and abort the plan.
2. ``apply_archive_patchset`` writes the patchset to disk. Each write
   uses tmp-then-``os.replace`` so an interrupted apply leaves either
   the old file or the new one — never a half-written file.

This split is what makes the operation atomic-enough for the PoC: any
*logical* failure (a substitution that produces an invalid page) is
caught before any I/O. The remaining failure modes are filesystem-level
(disk full mid-write), which the dry-run + tmp-replace strategy turns
into "stop early, recover by re-running."
"""

from __future__ import annotations

import difflib
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import frontmatter
import yaml
from pydantic import TypeAdapter

from engine.models.pages import ArchivedReason, Page, PageStatus
from engine.utils.wiki_walker import WikiPage, walk_wiki

_PAGE_ADAPTER = TypeAdapter(Page)


@dataclass(frozen=True)
class PagePatch:
    """One file's worth of changes."""

    path: Path
    old_metadata: dict[str, Any]
    new_metadata: dict[str, Any]
    old_body: str
    new_body: str

    @property
    def changed(self) -> bool:
        return self.old_metadata != self.new_metadata or self.old_body != self.new_body


@dataclass(frozen=True)
class ArchivePatchSet:
    """The complete plan for archiving one page.

    ``target_patch`` describes the archived page itself — same body,
    new ``status``/``archived_date``/``archived_reason`` frontmatter.
    ``referencing_patches`` describe every other page whose backlinks
    changed.
    """

    old_path: Path
    new_path: Path
    old_wikilink: str
    new_wikilink: str
    target_patch: PagePatch
    referencing_patches: list[PagePatch]

    @property
    def all_patches(self) -> list[PagePatch]:
        return [self.target_patch, *self.referencing_patches]


def _substitute_metadata(
    metadata: dict[str, Any], old_wikilink: str, new_wikilink: str
) -> dict[str, Any]:
    """Return a deep copy of ``metadata`` with `[[old]]` swapped for `[[new]]`.

    Substitution is exact-string: ``[[knowledge/decisions/X]]`` →
    ``[[knowledge/decisions/archived/X]]``. Any list-of-strings field
    (``related``, ``contradicts``, ``owners``) and the scalar
    ``supersedes`` are walked. Other values are passed through.
    """
    old = f"[[{old_wikilink}]]"
    new = f"[[{new_wikilink}]]"

    out: dict[str, Any] = {}
    for key, value in metadata.items():
        if isinstance(value, list):
            out[key] = [new if v == old else v for v in value]
        elif isinstance(value, str) and value == old:
            out[key] = new
        else:
            out[key] = value
    return out


def _substitute_body(body: str, old_wikilink: str, new_wikilink: str) -> str:
    """Replace exact ``[[old]]`` occurrences in body markdown."""
    return body.replace(f"[[{old_wikilink}]]", f"[[{new_wikilink}]]")


def _archive_target_metadata(
    metadata: dict[str, Any],
    *,
    archived_on: date,
    reason: ArchivedReason,
) -> dict[str, Any]:
    """Return ``metadata`` with status flipped to archived + provenance set."""
    out = dict(metadata)
    out["status"] = PageStatus.ARCHIVED.value
    out["archived_date"] = archived_on
    out["archived_reason"] = reason.value
    return out


def _archive_destination(
    target: WikiPage, root: Path, archived_subdir: str = "archived"
) -> tuple[Path, str]:
    """Compute the post-archive ``(new_path, new_wikilink)`` for ``target``.

    A page at ``knowledge/decisions/foo.md`` archives to
    ``knowledge/decisions/archived/foo.md``. If the page already lives
    inside an ``archived/`` subdir, the move is a no-op and we raise.
    """
    rel = target.path.relative_to(root)
    if archived_subdir in rel.parts:
        raise ValueError(f"page is already under {archived_subdir}/: {rel}")
    new_rel = rel.parent / archived_subdir / rel.name
    new_wikilink = str(new_rel.with_suffix("")).replace("\\", "/")
    return root / new_rel, new_wikilink


def compute_archive_patchset(
    wiki_root: Path,
    target_wikilink: str,
    *,
    archived_on: date,
    reason: ArchivedReason,
    archived_subdir: str = "archived",
) -> ArchivePatchSet:
    """Plan the archive of ``target_wikilink``. No disk writes.

    Walks ``wiki_root``, locates the target, computes its new path,
    then walks again to find every page that referenced the target
    (either via ``[[old]]`` in body or via the wikilink lists in
    frontmatter). Each candidate is patched and re-validated; a
    failure here means the plan is rejected and the caller can fix the
    seed before any I/O happens.
    """
    pages = list(walk_wiki(wiki_root))

    target: WikiPage | None = next((p for p in pages if p.wikilink == target_wikilink), None)
    if target is None:
        raise FileNotFoundError(
            f"target {target_wikilink!r} not found under {wiki_root} (walked {len(pages)} pages)"
        )

    new_path, new_wikilink = _archive_destination(target, wiki_root, archived_subdir)

    target_old_meta = frontmatter.load(target.path).metadata
    target_new_meta = _archive_target_metadata(
        target_old_meta, archived_on=archived_on, reason=reason
    )
    _PAGE_ADAPTER.validate_python(target_new_meta)
    target_patch = PagePatch(
        path=new_path,
        old_metadata=target_old_meta,
        new_metadata=target_new_meta,
        old_body=target.body,
        new_body=target.body,
    )

    referencing_patches: list[PagePatch] = []
    old_marker = f"[[{target_wikilink}]]"
    for page in pages:
        if page.wikilink == target_wikilink:
            continue

        old_meta = frontmatter.load(page.path).metadata
        new_meta = _substitute_metadata(old_meta, target_wikilink, new_wikilink)
        new_body = _substitute_body(page.body, target_wikilink, new_wikilink)

        if old_meta == new_meta and new_body == page.body:
            continue

        _PAGE_ADAPTER.validate_python(new_meta)

        if old_marker in new_body:
            raise AssertionError(
                f"residual reference to {target_wikilink!r} survived in {page.path} body"
            )

        referencing_patches.append(
            PagePatch(
                path=page.path,
                old_metadata=old_meta,
                new_metadata=new_meta,
                old_body=page.body,
                new_body=new_body,
            )
        )

    return ArchivePatchSet(
        old_path=target.path,
        new_path=new_path,
        old_wikilink=target_wikilink,
        new_wikilink=new_wikilink,
        target_patch=target_patch,
        referencing_patches=referencing_patches,
    )


def _dump_page(metadata: dict[str, Any], body: str) -> str:
    """Serialize a (frontmatter, body) pair back to markdown text."""
    fm = yaml.safe_dump(metadata, sort_keys=True, default_flow_style=False).rstrip("\n")
    body = body if body.startswith("\n") else f"\n{body}"
    return f"---\n{fm}\n---\n{body}"


def _atomic_write(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` via a sibling tmp file + ``os.replace``.

    A crash mid-write leaves either the old file or a tmp file behind
    — never a half-written real path.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def apply_archive_patchset(patchset: ArchivePatchSet) -> None:
    """Execute a planned patchset on disk.

    Order:
    1. Write the (rewritten) target page at its new path.
    2. Delete the old target file.
    3. Write each referencing patch atomically.

    Any failure leaves a clear partial state. Re-running the plan from
    scratch will surface what's still pending.
    """
    target_text = _dump_page(patchset.target_patch.new_metadata, patchset.target_patch.new_body)
    _atomic_write(patchset.new_path, target_text)

    if patchset.old_path.exists() and patchset.old_path != patchset.new_path:
        patchset.old_path.unlink()

    for patch in patchset.referencing_patches:
        text = _dump_page(patch.new_metadata, patch.new_body)
        _atomic_write(patch.path, text)


def render_patchset_diff(patchset: ArchivePatchSet, *, context_lines: int = 3) -> str:
    """Return a human-readable unified-diff dump for every changed file.

    Useful for the dry-run cell in NB 08: print the return value to see
    every prospective change before calling ``apply_archive_patchset``.
    """
    chunks: list[str] = []

    move_header = f"# move\n- {patchset.old_path}\n+ {patchset.new_path}\n"
    chunks.append(move_header)

    for patch in patchset.all_patches:
        old = _dump_page(patch.old_metadata, patch.old_body).splitlines(keepends=True)
        new = _dump_page(patch.new_metadata, patch.new_body).splitlines(keepends=True)
        diff = difflib.unified_diff(
            old,
            new,
            fromfile=str(patch.path),
            tofile=str(patch.path),
            n=context_lines,
        )
        chunk = "".join(diff)
        if chunk:
            chunks.append(chunk)

    return "\n".join(chunks)


def copy_wiki_to(src: Path, dst: Path) -> Path:
    """Convenience: deep-copy ``src`` (a wiki root) to ``dst``.

    Used by the notebook to demo archive on a /tmp/ copy without
    touching canonical fixtures. Returns ``dst``.
    """
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    return dst


def all_referenced_wikilinks(pages: Iterable[WikiPage]) -> set[str]:
    """Collect every distinct ``[[wikilink]]`` referenced anywhere in the corpus.

    Inverse of orphan detection: a page whose ``wikilink`` isn't in
    this set has no inbound references. Lint uses this to flag orphans.
    """
    referenced: set[str] = set()
    for page in pages:
        for field in ("related", "contradicts", "owners"):
            for raw in getattr(page.page, field, []) or []:
                if raw.startswith("[[") and raw.endswith("]]"):
                    referenced.add(raw[2:-2])
        if page.page.supersedes:
            inner = page.page.supersedes
            if inner.startswith("[[") and inner.endswith("]]"):
                referenced.add(inner[2:-2])
        # body wikilinks
        for token in page.body.split("[["):
            end = token.find("]]")
            if end != -1:
                referenced.add(token[:end])
    return referenced


__all__ = [
    "ArchivePatchSet",
    "PagePatch",
    "all_referenced_wikilinks",
    "apply_archive_patchset",
    "compute_archive_patchset",
    "copy_wiki_to",
    "render_patchset_diff",
]
