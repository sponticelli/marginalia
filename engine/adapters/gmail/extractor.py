"""Gmail adapter — fetch a thread by ID and render as markdown.

Why threads, not single messages: decisions almost never live in one
email. They emerge across a back-and-forth. The Gmail API exposes a
``threads.get`` endpoint that returns every message in the thread with
parsed headers and body parts — exactly what we need to ingest
"the discussion that led to X."

Quote-stripping: by default we drop reply chains (``>``-prefixed
lines, plus Gmail's ``On <date>, <name> wrote:`` blocks) since
quoting bloats the text without adding signal — Marginalia stores the
canonical thread once, not N copies of every prior reply nested
deeper. ``include_quotes=True`` keeps them for the rare case where
the quote body contains content the original sender redacted.
"""

from __future__ import annotations

import asyncio
import base64
import re
from typing import TYPE_CHECKING, Any

from engine.adapters._template.contract import ExtractedContent

if TYPE_CHECKING:
    from googleapiclient.discovery import Resource

# Gmail thread/message IDs are hex; the API accepts both interchangeably
# in `threads.get` (Gmail uses the same ID space). 16 hex chars is a
# safe minimum length.
_GMAIL_ID_RE = re.compile(r"^[0-9a-fA-F]{16,}$")
# Gmail's web URL form: ...#inbox/<thread-id> or #search/<q>/<thread-id>
_URL_GMAIL_ID_RE = re.compile(r"#[a-z]+/(?:[^/]+/)?([0-9a-fA-F]{16,})")
# Strip Gmail's standard quote header line.
_QUOTE_HEADER_RE = re.compile(
    r"^On .+ at .+, .+ wrote:\s*$|^On .+, .+ wrote:\s*$",
    re.MULTILINE,
)


class GmailExtractError(ValueError):
    """Raised when input isn't a recognizable Gmail URL or ID."""


def extract_gmail_id(url_or_id: str) -> str:
    """Pull the thread/message ID from a Gmail URL or accept it bare."""
    if _GMAIL_ID_RE.match(url_or_id):
        return url_or_id
    match = _URL_GMAIL_ID_RE.search(url_or_id)
    if match:
        return match.group(1)
    raise GmailExtractError(
        f"could not extract Gmail thread/message ID from {url_or_id!r}; "
        "expected a mail.google.com URL or a bare hex ID"
    )


def _build_gmail_service(credentials):
    from googleapiclient.discovery import build

    return build("gmail", "v1", credentials=credentials, cache_discovery=False)


def _decode_body(part: dict) -> str:
    """Decode one MIME part's body to UTF-8 text. Returns '' for non-text parts."""
    body = part.get("body", {}) or {}
    data = body.get("data")
    if not data:
        return ""
    # Gmail base64url-encodes message bodies.
    raw = base64.urlsafe_b64decode(data + "==")
    try:
        return raw.decode("utf-8", errors="replace")
    except (UnicodeDecodeError, AttributeError):
        return ""


def _extract_text_from_payload(payload: dict) -> str:
    """Walk a message payload's MIME tree, prefer text/plain, fall back to text/html→stripped.

    Gmail represents multipart messages as a tree under ``parts``;
    each leaf has its own ``mimeType`` + body. Real-world messages
    often nest multipart/alternative inside multipart/mixed, so we
    recurse rather than assume a flat structure.
    """
    mime = payload.get("mimeType", "")
    if mime.startswith("text/plain"):
        return _decode_body(payload)
    if mime.startswith("text/html"):
        # Crude HTML strip — keeps content readable without pulling lxml.
        # Production users wanting fidelity should configure Gmail clients
        # to send plaintext alternatives (which most do by default).
        html = _decode_body(payload)
        return re.sub(r"<[^>]+>", "", html).strip()
    if "parts" in payload:
        # Prefer plain over html when both are present (multipart/alternative).
        plain_parts = [
            p for p in payload["parts"] if p.get("mimeType", "").startswith("text/plain")
        ]
        if plain_parts:
            return "\n\n".join(_extract_text_from_payload(p) for p in plain_parts)
        return "\n\n".join(_extract_text_from_payload(p) for p in payload["parts"])
    return ""


def _strip_quotes(body: str) -> str:
    """Remove Gmail-style quote headers and ``>``-prefixed lines."""
    body = _QUOTE_HEADER_RE.sub("", body)
    kept = [line for line in body.splitlines() if not line.lstrip().startswith(">")]
    # Collapse runs of blank lines left by the strip.
    out: list[str] = []
    blank = 0
    for line in kept:
        if not line.strip():
            blank += 1
            if blank <= 1:
                out.append("")
        else:
            blank = 0
            out.append(line)
    return "\n".join(out).strip()


def _header(message: dict, name: str) -> str:
    """Lookup a header by name (case-insensitive); returns '' if missing."""
    headers = (message.get("payload", {}) or {}).get("headers", []) or []
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _format_message(message: dict, *, include_quotes: bool) -> str:
    """Render one Gmail message as a markdown block."""
    sender = _header(message, "From")
    date = _header(message, "Date")
    subject = _header(message, "Subject")
    body = _extract_text_from_payload(message.get("payload", {}) or {}).strip()
    if not include_quotes:
        body = _strip_quotes(body)
    head = f"### {sender or '(unknown sender)'} — {date or '(no date)'}"
    if subject:
        head += f"\n*Subject: {subject}*"
    return f"{head}\n\n{body or '(empty body)'}\n"


def _failure(reason: str) -> ExtractedContent:
    return ExtractedContent(
        text="",
        pages=None,
        extraction_method="text",
        failure_reason=reason,
    )


def _fetch_thread(service: Resource, thread_id: str) -> dict[str, Any]:
    """Sync GET /users/me/threads/{id} with full message bodies."""
    return service.users().threads().get(userId="me", id=thread_id, format="full").execute()


async def extract_gmail(
    url_or_id: str,
    *,
    credentials=None,
    service: Resource | None = None,
    include_quotes: bool = False,
) -> ExtractedContent:
    """Fetch a Gmail thread; render every message as markdown.

    Auth resolution mirrors ``extract_google_doc``: explicit
    ``service`` first (test path), then ``credentials``, then cached
    token via ``_google_oauth.load_credentials()``.

    The ``pages`` field is populated with one entry per message so
    downstream cost-tracking + cache-keying can reason per-message
    when needed.
    """
    try:
        thread_id = extract_gmail_id(url_or_id)
    except GmailExtractError as exc:
        return _failure(f"invalid_thread_id: {exc}")

    if service is None:
        if credentials is None:
            from engine.adapters._google_oauth import GoogleAuthError, load_credentials

            try:
                credentials = load_credentials()
            except GoogleAuthError as exc:
                return _failure(f"auth_failed: {exc}")
        service = _build_gmail_service(credentials)

    try:
        thread = await asyncio.to_thread(_fetch_thread, service, thread_id)
    except Exception as exc:  # noqa: BLE001
        return _failure(f"thread_fetch_failed: {type(exc).__name__}: {exc}")

    messages = thread.get("messages", []) or []
    if not messages:
        return _failure("empty_thread")

    rendered = [_format_message(m, include_quotes=include_quotes) for m in messages]
    subject = _header(messages[0], "Subject") or "(no subject)"
    body = (
        f"# {subject}\n\n"
        f"Source: Gmail thread {thread_id}\n"
        f"Messages: {len(messages)}\n\n" + "\n---\n\n".join(rendered)
    )

    return ExtractedContent(
        text=body,
        pages=rendered,
        extraction_method="text",
        cost_usd=None,
        failure_reason=None,
    )


__all__ = [
    "GmailExtractError",
    "extract_gmail",
    "extract_gmail_id",
]
