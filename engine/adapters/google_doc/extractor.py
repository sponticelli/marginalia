"""Google Docs adapter — Doc by ID → markdown via Drive's export endpoint.

Scope: a single Google Doc (by document ID or `docs.google.com` URL).
This narrow scope cleanly covers Google Meet transcripts, which land
as Docs in a "Meet Recordings" folder — same adapter, no special
casing.

Why this is one HTTP call: Google Docs added native ``text/markdown``
export in 2024. We hit ``/files/{id}/export?mimeType=text/markdown``,
the response IS the markdown body, no parsing needed. Title and
last-modified come from a separate ``/files/{id}`` metadata call.

Auth flows through the shared ``_google_oauth`` helper. Tests pass an
explicit ``service`` (the Drive service object) and skip auth.
"""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING

from engine.adapters._template.contract import ExtractedContent

if TYPE_CHECKING:
    from googleapiclient.discovery import Resource

# Google Doc IDs are 25-100 chars of base64-url alphabet (no padding).
# The regex tolerates the realistic range; the Drive API will reject
# anything actually invalid.
_DOC_ID_RE = re.compile(r"^[A-Za-z0-9_-]{20,100}$")
_URL_DOC_ID_RE = re.compile(r"/document/d/([A-Za-z0-9_-]{20,100})")


class GoogleDocExtractError(ValueError):
    """Raised when input isn't a recognizable Google Doc URL or ID."""


def extract_doc_id(url_or_id: str) -> str:
    """Pull the Doc ID from a docs.google.com URL or accept it bare.

    Accepts:
    - https://docs.google.com/document/d/<id>/edit
    - https://docs.google.com/document/d/<id>
    - <id> directly (already extracted)
    """
    if _DOC_ID_RE.match(url_or_id):
        return url_or_id

    match = _URL_DOC_ID_RE.search(url_or_id)
    if match:
        return match.group(1)

    raise GoogleDocExtractError(
        f"could not extract Google Doc ID from {url_or_id!r}; "
        "expected a docs.google.com URL or a bare doc ID"
    )


def _build_drive_service(credentials):
    """Construct a Drive v3 service from already-authorized credentials."""
    from googleapiclient.discovery import build

    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def _fetch_doc_metadata(service: Resource, doc_id: str) -> dict:
    """Sync call: GET /files/{id} with title + modifiedTime."""
    return service.files().get(fileId=doc_id, fields="id,name,modifiedTime,mimeType").execute()


def _fetch_doc_markdown(service: Resource, doc_id: str) -> str:
    """Sync call: GET /files/{id}/export?mimeType=text/markdown.

    The export endpoint returns raw bytes; for ``text/markdown`` they're
    UTF-8 markdown. Decoding here keeps the public API string-typed.
    """
    blob: bytes = service.files().export(fileId=doc_id, mimeType="text/markdown").execute()
    return blob.decode("utf-8") if isinstance(blob, bytes) else str(blob)


def _failure(reason: str) -> ExtractedContent:
    return ExtractedContent(
        text="",
        pages=None,
        extraction_method="text",
        failure_reason=reason,
    )


async def extract_google_doc(
    url_or_id: str,
    *,
    credentials=None,
    service: Resource | None = None,
) -> ExtractedContent:
    """Fetch a Google Doc by URL or ID; return markdown ``ExtractedContent``.

    Auth resolution order:

    1. ``service`` (a pre-built Drive service) — the test path.
    2. ``credentials`` (a ``Credentials`` instance) — used to build a
       service inline.
    3. Cached token via ``_google_oauth.load_credentials()`` — the
       production path.

    Returns ``ExtractedContent`` with ``extraction_method="text"`` (the
    markdown came from Google's text export, not from vision).
    """
    try:
        doc_id = extract_doc_id(url_or_id)
    except GoogleDocExtractError as exc:
        return _failure(f"invalid_doc_id: {exc}")

    if service is None:
        if credentials is None:
            from engine.adapters._google_oauth import GoogleAuthError, load_credentials

            try:
                credentials = load_credentials()
            except GoogleAuthError as exc:
                return _failure(f"auth_failed: {exc}")
        service = _build_drive_service(credentials)

    try:
        metadata = await asyncio.to_thread(_fetch_doc_metadata, service, doc_id)
    except Exception as exc:  # noqa: BLE001
        return _failure(f"metadata_fetch_failed: {type(exc).__name__}: {exc}")

    if metadata.get("mimeType") != "application/vnd.google-apps.document":
        return _failure(
            f"not_a_google_doc: {doc_id} has mimeType "
            f"{metadata.get('mimeType')!r} — only Docs support markdown export"
        )

    try:
        markdown = await asyncio.to_thread(_fetch_doc_markdown, service, doc_id)
    except Exception as exc:  # noqa: BLE001
        return _failure(f"export_failed: {type(exc).__name__}: {exc}")

    if not markdown.strip():
        return _failure("empty_doc")

    title = metadata.get("name", "Untitled")
    modified = metadata.get("modifiedTime", "unknown")
    body = (
        f"# {title}\n\n"
        f"Source: https://docs.google.com/document/d/{doc_id}\n"
        f"Last modified: {modified}\n\n"
        f"{markdown}"
    )

    return ExtractedContent(
        text=body,
        pages=None,
        extraction_method="text",
        cost_usd=None,
        failure_reason=None,
    )


__all__ = [
    "GoogleDocExtractError",
    "extract_doc_id",
    "extract_google_doc",
]
