"""``marginalia.search`` — index-backed search over a wiki directory (design §7.4).

Term-overlap + length-normalized scoring; pure Python, no extra deps.
The PoC corpus is small (5-20 pages); when scale matters, the
`SearchHit` shape is stable enough to swap in a vector or BM25 backend
behind the same function signature.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import TYPE_CHECKING

import frontmatter
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from engine.models.pages import Page, PageType

_PAGE_ADAPTER = TypeAdapter(Page)

if TYPE_CHECKING:
    pass

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_-]+", flags=re.ASCII)
_MIN_TOKEN_LEN = 3
_DEFAULT_LIMIT = 10
_SNIPPET_LEN = 240


class SearchHit(BaseModel):
    """One result from ``search``. Stable shape across backends."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(description="Wiki path without extension (e.g. 'sources/foo').")
    title: str
    type: PageType
    snippet: str
    score: float = Field(ge=0.0)


def _tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if len(t) >= _MIN_TOKEN_LEN]


def _index_pages(wiki_root: Path) -> list[tuple[str, Page, str, list[str]]]:
    """Walk wiki_root, return list of (path_no_ext, page, body, tokens)."""
    out: list[tuple[str, Page, str, list[str]]] = []
    for md_path in sorted(wiki_root.rglob("*.md")):
        if not md_path.is_file():
            continue
        try:
            post = frontmatter.load(md_path)
            page = _PAGE_ADAPTER.validate_python(post.metadata)
        except Exception:  # noqa: BLE001 — skip unparseable, don't crash search
            continue
        body = post.content
        tokens = _tokenize(f"{page.title}\n{body}\n{' '.join(page.tags or [])}")
        rel = md_path.relative_to(wiki_root).with_suffix("")
        out.append((str(rel), page, body, tokens))
    return out


def _score(query_tokens: list[str], page_tokens: list[str], idf: dict[str, float]) -> float:
    """TF-IDF-shaped: term frequency × IDF, length-normalized."""
    if not query_tokens or not page_tokens:
        return 0.0
    counts: dict[str, int] = {}
    for t in page_tokens:
        counts[t] = counts.get(t, 0) + 1
    raw = sum(counts.get(t, 0) * idf.get(t, 0.0) for t in query_tokens)
    return raw / math.sqrt(len(page_tokens)) if raw > 0 else 0.0


def _build_idf(corpus: list[list[str]]) -> dict[str, float]:
    n_docs = len(corpus) or 1
    df: dict[str, int] = {}
    for tokens in corpus:
        for t in set(tokens):
            df[t] = df.get(t, 0) + 1
    return {t: math.log((n_docs + 1) / (count + 1)) + 1.0 for t, count in df.items()}


def _make_snippet(body: str, query_tokens: list[str]) -> str:
    body = body.strip().replace("\n", " ")
    if not query_tokens or not body:
        return body[:_SNIPPET_LEN]
    lower = body.lower()
    for t in query_tokens:
        idx = lower.find(t)
        if idx == -1:
            continue
        start = max(0, idx - 60)
        end = min(len(body), idx + 180)
        prefix = "…" if start > 0 else ""
        suffix = "…" if end < len(body) else ""
        return f"{prefix}{body[start:end]}{suffix}"
    return body[:_SNIPPET_LEN]


def search(
    query: str,
    *,
    wiki_root: Path,
    type: PageType | None = None,
    limit: int = _DEFAULT_LIMIT,
) -> list[SearchHit]:
    """Search the wiki corpus by term overlap. Returns top-``limit`` hits."""
    if not query.strip():
        return []

    pages = _index_pages(wiki_root)
    if type is not None:
        pages = [p for p in pages if p[1].type == type]
    if not pages:
        return []

    idf = _build_idf([tokens for _, _, _, tokens in pages])
    query_tokens = _tokenize(query)

    scored: list[tuple[str, Page, str, float]] = []
    for path_no_ext, page, body, tokens in pages:
        score = _score(query_tokens, tokens, idf)
        if score > 0:
            scored.append((path_no_ext, page, body, score))

    scored.sort(key=lambda t: t[3], reverse=True)
    return [
        SearchHit(
            path=path_no_ext,
            title=page.title,
            type=page.type,
            snippet=_make_snippet(body, query_tokens),
            score=score,
        )
        for path_no_ext, page, body, score in scored[:limit]
    ]


__all__ = ["SearchHit", "search"]
