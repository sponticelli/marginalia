"""Slack adapter — fetch a thread by archive URL and render as markdown.

Slack archive URLs come in two forms relevant to ingest:

- ``https://<workspace>.slack.com/archives/C012345/p1234567890123456``
  → a single message (or a top-level message + replies if it's a parent).
- ``https://<workspace>.slack.com/archives/C012345/p1234567890123456?thread_ts=1234567890.123456``
  → an explicit thread anchor.

The adapter resolves the URL to ``(channel_id, thread_ts)``, fetches
``conversations.replies`` for the full thread, and renders each
message as a markdown block with sender/timestamp headers. User and
channel ID resolution to display names happens via separate
``users.info`` / ``conversations.info`` calls (cached per-call so a
chatty thread doesn't fan out into N+1 requests for the same user).

Auth: ``SLACK_BOT_TOKEN`` env var (must have the ``channels:history``,
``groups:history``, ``users:read``, ``channels:read`` scopes).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

from engine.adapters._template.contract import ExtractedContent

DEFAULT_API_BASE = "https://slack.com/api"
DEFAULT_TIMEOUT_S = 30.0
TOKEN_ENV = "SLACK_BOT_TOKEN"

# Archive-URL shape: /archives/<channel_id>/p<unix-ts-with-microseconds>
# Optional ?thread_ts= qualifier for thread-specific deep-links.
_ARCHIVE_URL_RE = re.compile(
    r"https?://[^/]+\.slack\.com/archives/([A-Z0-9]+)/p(\d+)(?:\?thread_ts=([\d.]+))?",
)
# Slack mention syntax in message text: <@U012345>, <#C012345|name>, <https://url|label>
_MENTION_USER_RE = re.compile(r"<@([UW][A-Z0-9]+)>")
_MENTION_CHANNEL_RE = re.compile(r"<#([CG][A-Z0-9]+)\|?([^>]*)>")
_LINK_RE = re.compile(r"<(https?://[^|>]+)\|?([^>]*)>")


class SlackExtractError(ValueError):
    """Raised when input isn't a recognizable Slack archive URL."""


@dataclass(frozen=True)
class SlackThreadRef:
    """Resolved (channel_id, thread_ts) pair from a Slack archive URL."""

    channel_id: str
    thread_ts: str


def _ts_from_archive_p(p_ts: str) -> str:
    """Convert ``p1234567890123456`` (URL form) → ``1234567890.123456`` (API form)."""
    # Slack URLs encode the dot-separated timestamp by stripping the dot
    # and inserting a microsecond chunk after the last 6 digits.
    if len(p_ts) < 7:
        raise SlackExtractError(f"malformed Slack timestamp in URL: p{p_ts!r}")
    return f"{p_ts[:-6]}.{p_ts[-6:]}"


def parse_slack_url(url: str) -> SlackThreadRef:
    """Extract the (channel, thread_ts) pair from a Slack archive URL."""
    match = _ARCHIVE_URL_RE.search(url)
    if not match:
        raise SlackExtractError(
            f"could not parse Slack archive URL {url!r}; "
            "expected '<workspace>.slack.com/archives/<channel>/p<ts>'"
        )
    channel_id, p_ts, explicit_thread_ts = match.groups()
    thread_ts = explicit_thread_ts if explicit_thread_ts else _ts_from_archive_p(p_ts)
    return SlackThreadRef(channel_id=channel_id, thread_ts=thread_ts)


# ─── name resolution + rendering ────────────────────────────────────


def _format_message_text(text: str, *, users: dict[str, str], channels: dict[str, str]) -> str:
    """Substitute user/channel/link mentions for human-readable forms."""

    def _user_sub(m: re.Match) -> str:
        uid = m.group(1)
        name = users.get(uid)
        return f"@{name}" if name else f"@{uid}"

    def _channel_sub(m: re.Match) -> str:
        cid, label = m.group(1), m.group(2)
        name = label or channels.get(cid, cid)
        return f"#{name}"

    def _link_sub(m: re.Match) -> str:
        url, label = m.group(1), m.group(2)
        return f"[{label or url}]({url})"

    text = _MENTION_USER_RE.sub(_user_sub, text)
    text = _MENTION_CHANNEL_RE.sub(_channel_sub, text)
    text = _LINK_RE.sub(_link_sub, text)
    return text


def _format_message(msg: dict, *, users: dict[str, str], channels: dict[str, str]) -> str:
    """Render one Slack message as a markdown block."""
    user_id = msg.get("user") or msg.get("bot_id") or "unknown"
    sender = users.get(user_id, user_id)
    ts = msg.get("ts", "?")
    text = _format_message_text(msg.get("text", ""), users=users, channels=channels)
    head = f"### @{sender} — ts {ts}"

    files = msg.get("files", []) or []
    file_lines: list[str] = []
    for f in files:
        name = f.get("name", "(file)")
        url = f.get("url_private") or f.get("permalink") or "(no url)"
        file_lines.append(f"- 📎 [{name}]({url})")

    block = f"{head}\n\n{text or '(no text)'}\n"
    if file_lines:
        block += "\n**Files:**\n" + "\n".join(file_lines) + "\n"
    return block


def _failure(reason: str) -> ExtractedContent:
    return ExtractedContent(
        text="",
        pages=None,
        extraction_method="text",
        failure_reason=reason,
    )


# ─── HTTP plumbing ──────────────────────────────────────────────────


async def _slack_get(client, method: str, params: dict[str, Any]) -> dict:
    """GET /api/<method>?<params>; raise on Slack-level error.

    Slack uses HTTP 200 for both success and errors — the failure
    signal is in the JSON body's ``ok`` field. Raising on
    ``ok=false`` lets the caller's try/except cover both transport
    and application errors.
    """
    resp = await client.get(f"/{method}", params=params)
    resp.raise_for_status()
    payload = resp.json()
    if not payload.get("ok"):
        raise RuntimeError(f"slack {method} error: {payload.get('error', 'unknown')}")
    return payload


async def _resolve_users(client, user_ids: set[str]) -> dict[str, str]:
    """Look up display names for ``user_ids`` via users.info, batched."""
    out: dict[str, str] = {}
    for uid in user_ids:
        try:
            data = await _slack_get(client, "users.info", {"user": uid})
        except Exception:  # noqa: BLE001
            continue
        user = data.get("user", {}) or {}
        profile = user.get("profile", {}) or {}
        out[uid] = (
            profile.get("display_name") or profile.get("real_name") or user.get("name") or uid
        )
    return out


async def _resolve_channel(client, channel_id: str) -> str:
    """Look up a channel's display name via conversations.info."""
    try:
        data = await _slack_get(client, "conversations.info", {"channel": channel_id})
    except Exception:  # noqa: BLE001
        return channel_id
    return (data.get("channel", {}) or {}).get("name", channel_id)


async def _fetch_thread(client, channel_id: str, thread_ts: str) -> list[dict]:
    """Page through conversations.replies until cursor exhausts."""
    out: list[dict] = []
    cursor: str | None = None
    while True:
        params: dict[str, Any] = {
            "channel": channel_id,
            "ts": thread_ts,
            "limit": 200,
        }
        if cursor:
            params["cursor"] = cursor
        data = await _slack_get(client, "conversations.replies", params)
        out.extend(data.get("messages", []) or [])
        if not data.get("has_more"):
            break
        cursor = (data.get("response_metadata", {}) or {}).get("next_cursor")
        if not cursor:
            break
    return out


# ─── public entry ───────────────────────────────────────────────────


async def extract_slack(
    url: str,
    *,
    token: str | None = None,
    api_base: str = DEFAULT_API_BASE,
    client=None,
) -> ExtractedContent:
    """Fetch a Slack thread by archive URL → markdown ``ExtractedContent``.

    The ``client`` argument lets tests inject an ``httpx.AsyncClient``
    backed by ``MockTransport``.
    """
    try:
        ref = parse_slack_url(url)
    except SlackExtractError as exc:
        return _failure(f"invalid_url: {exc}")

    auth_token = token or os.environ.get(TOKEN_ENV)
    if not auth_token:
        return _failure(f"auth_failed: missing {TOKEN_ENV} env var (Slack bot token)")

    import httpx

    own_client = client is None
    if client is None:
        client = httpx.AsyncClient(
            base_url=api_base,
            headers={"Authorization": f"Bearer {auth_token}"},
            timeout=DEFAULT_TIMEOUT_S,
        )

    try:
        try:
            messages = await _fetch_thread(client, ref.channel_id, ref.thread_ts)
        except httpx.HTTPStatusError as exc:
            return _failure(f"thread_fetch_failed: HTTP {exc.response.status_code}")
        except Exception as exc:  # noqa: BLE001
            return _failure(f"thread_fetch_failed: {type(exc).__name__}: {exc}")

        if not messages:
            return _failure("empty_thread")

        # Resolve sender names + channel name once for the whole thread.
        user_ids = {m.get("user") for m in messages if m.get("user")}
        users = await _resolve_users(client, {uid for uid in user_ids if uid})
        channel_name = await _resolve_channel(client, ref.channel_id)
    finally:
        if own_client:
            await client.aclose()

    rendered = [
        _format_message(m, users=users, channels={ref.channel_id: channel_name}) for m in messages
    ]
    body = (
        f"# Slack thread in #{channel_name}\n\n"
        f"Source: {url}\n"
        f"Thread ts: {ref.thread_ts}\n"
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
    "DEFAULT_API_BASE",
    "TOKEN_ENV",
    "SlackExtractError",
    "SlackThreadRef",
    "extract_slack",
    "parse_slack_url",
]
