"""Apple Notes adapter — read notes via AppleScript (macOS only).

Mechanism: ``osascript -l JavaScript`` runs JXA against the Notes app,
which exposes ``Application('Notes').notes.whose(...)`` queries that
return note bodies as HTML. We extract by title (most ergonomic for
quick capture) and convert the HTML to markdown via a tiny regex
pipeline — Apple's HTML is mechanically simple (h1/h2/h3, p, ul/ol/li,
br, strong/em, blockquote, a) and a full HTML parser would be
overkill for the actual shapes that come out.

URL scheme: ``notes://<title>`` — matches Apple's own ``notes://`` URL
scheme that the Notes app registers, though we use it as a Marginalia
input-shape rather than launching the app.

Platform check: raises ``AppleNotesUnsupportedPlatform`` on non-Darwin
so tests on Linux CI can skip cleanly via the exception type.
"""

from __future__ import annotations

import asyncio
import re
import subprocess
import sys
from typing import Literal

from engine.adapters._template.contract import ExtractedContent

DEFAULT_TIMEOUT_S = 30
NOTES_URL_SCHEME = "notes"


class AppleNotesExtractError(ValueError):
    """Raised when input isn't a recognizable Notes URL or title."""


class AppleNotesUnsupportedPlatform(RuntimeError):
    """Raised on non-Darwin (Linux/Windows). Adapter is macOS-only."""


# JXA script that fetches one note by title and prints its body HTML
# to stdout. Title matching is exact (case-sensitive); fuzzy/partial
# matching is a deliberate non-goal — quick-capture should hit the
# right note or fail loud.
_JXA_GET_BY_TITLE = r"""
function run(argv) {
    var title = argv[0];
    var Notes = Application('Notes');
    Notes.includeStandardAdditions = true;
    var matches = Notes.notes.whose({name: title});
    if (matches.length === 0) {
        return JSON.stringify({error: 'not_found', title: title});
    }
    var note = matches[0];
    return JSON.stringify({
        ok: true,
        title: note.name(),
        body: note.body(),
        modificationDate: String(note.modificationDate()),
    });
}
"""

# JXA script that lists every note in a named folder (for the
# ``--notes-folder`` flow). Returns one line of JSON per note.
_JXA_LIST_FOLDER = r"""
function run(argv) {
    var folderName = argv[0];
    var Notes = Application('Notes');
    var folders = Notes.folders.whose({name: folderName});
    if (folders.length === 0) {
        return JSON.stringify({error: 'folder_not_found', folder: folderName});
    }
    var notes = folders[0].notes;
    var out = [];
    for (var i = 0; i < notes.length; i++) {
        out.push({
            title: notes[i].name(),
            body: notes[i].body(),
            modificationDate: String(notes[i].modificationDate()),
        });
    }
    return JSON.stringify({ok: true, notes: out});
}
"""

ScriptName = Literal["get_by_title", "list_folder"]


def _platform_check() -> None:
    if sys.platform != "darwin":
        raise AppleNotesUnsupportedPlatform(
            f"apple_notes adapter requires macOS; current platform is {sys.platform!r}"
        )


def _run_jxa(script: str, *args: str, timeout_s: int) -> str:
    """Run a JXA script via osascript; return stdout. Raises on non-zero."""
    proc = subprocess.run(
        ["osascript", "-l", "JavaScript", "-e", script, *args],
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"osascript failed (exit {proc.returncode}): {proc.stderr.strip()}")
    return proc.stdout.strip()


# ─── HTML → markdown ────────────────────────────────────────────────


def html_to_markdown(html: str) -> str:
    """Convert Apple Notes' simple HTML to markdown.

    Handles the tags Notes actually emits: h1/h2/h3, p, ul/ol/li, br,
    strong/b, em/i, blockquote, a, plus the wrapping <div> that Notes
    uses as the document root. Anything else falls through with the
    tags stripped — better than dropping content silently.
    """
    out = html
    # Block-level → markdown headers/paragraphs.
    out = re.sub(r"<h1[^>]*>(.*?)</h1>", r"# \1\n", out, flags=re.DOTALL | re.IGNORECASE)
    out = re.sub(r"<h2[^>]*>(.*?)</h2>", r"## \1\n", out, flags=re.DOTALL | re.IGNORECASE)
    out = re.sub(r"<h3[^>]*>(.*?)</h3>", r"### \1\n", out, flags=re.DOTALL | re.IGNORECASE)
    out = re.sub(
        r"<blockquote[^>]*>(.*?)</blockquote>", r"> \1\n", out, flags=re.DOTALL | re.IGNORECASE
    )
    out = re.sub(r"<p[^>]*>(.*?)</p>", r"\1\n", out, flags=re.DOTALL | re.IGNORECASE)
    out = re.sub(r"<br\s*/?>", "\n", out, flags=re.IGNORECASE)
    # Inline emphasis.
    out = re.sub(r"<(strong|b)[^>]*>(.*?)</\1>", r"**\2**", out, flags=re.DOTALL | re.IGNORECASE)
    out = re.sub(r"<(em|i)[^>]*>(.*?)</\1>", r"*\2*", out, flags=re.DOTALL | re.IGNORECASE)
    # Links: <a href="...">label</a> → [label](href).
    out = re.sub(
        r'<a\s+[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        r"[\2](\1)",
        out,
        flags=re.DOTALL | re.IGNORECASE,
    )
    # Lists: handle <ul>/<ol> wrappers + <li> items.
    out = re.sub(r"<li[^>]*>(.*?)</li>", r"- \1\n", out, flags=re.DOTALL | re.IGNORECASE)
    out = re.sub(r"</?ul[^>]*>", "", out, flags=re.IGNORECASE)
    out = re.sub(r"</?ol[^>]*>", "", out, flags=re.IGNORECASE)
    # Strip remaining tags (divs, spans, font, etc.).
    out = re.sub(r"<[^>]+>", "", out)
    # Decode common HTML entities the regex pipeline doesn't touch.
    out = (
        out.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    # Collapse runs of blank lines from the strip.
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    return out


# ─── public entry ───────────────────────────────────────────────────


def _failure(reason: str) -> ExtractedContent:
    return ExtractedContent(
        text="",
        pages=None,
        extraction_method="text",
        failure_reason=reason,
    )


def _strip_url_prefix(input_str: str) -> str:
    """Accept either ``notes://<title>`` or a bare title."""
    if input_str.startswith(f"{NOTES_URL_SCHEME}://"):
        return input_str[len(f"{NOTES_URL_SCHEME}://") :]
    return input_str


async def extract_apple_note(
    input_str: str,
    *,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    runner=None,
) -> ExtractedContent:
    """Fetch one Apple Note by title or notes:// URL → markdown.

    ``runner`` is the test injection point: a callable
    ``(script, *args, timeout_s) -> str`` that returns the JXA stdout
    directly. Production callers leave it ``None`` and the adapter
    shells out to ``osascript``.
    """
    if runner is None:
        # Only enforce platform when actually shelling out — tests on
        # Linux can pass a custom runner and exercise the parsing path.
        try:
            _platform_check()
        except AppleNotesUnsupportedPlatform as exc:
            return _failure(f"unsupported_platform: {exc}")

    title = _strip_url_prefix(input_str.strip())
    if not title:
        return _failure("invalid_input: empty title")

    import json

    try:
        if runner is not None:
            stdout = runner(_JXA_GET_BY_TITLE, title, timeout_s=timeout_s)
        else:
            stdout = await asyncio.to_thread(
                _run_jxa, _JXA_GET_BY_TITLE, title, timeout_s=timeout_s
            )
    except subprocess.TimeoutExpired:
        return _failure(f"timeout: osascript exceeded {timeout_s}s")
    except FileNotFoundError:
        return _failure("osascript_missing: osascript not on PATH")
    except RuntimeError as exc:
        return _failure(f"osascript_failed: {exc}")

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        return _failure(f"parse_failed: osascript stdout was not JSON ({exc})")

    if data.get("error") == "not_found":
        return _failure(f"not_found: no note titled {title!r}")
    if not data.get("ok"):
        return _failure(f"unknown_response: {data!r}")

    note_title = data.get("title", title)
    note_body_html = data.get("body", "")
    modification_date = data.get("modificationDate", "unknown")
    body_md = html_to_markdown(note_body_html)
    if not body_md:
        return _failure("empty_note")

    full = (
        f"# {note_title}\n\n"
        f"Source: Apple Notes — `{title}`\n"
        f"Modified: {modification_date}\n\n"
        f"{body_md}\n"
    )

    return ExtractedContent(
        text=full,
        pages=None,
        extraction_method="text",
        cost_usd=None,
        failure_reason=None,
    )


__all__ = [
    "AppleNotesExtractError",
    "AppleNotesUnsupportedPlatform",
    "DEFAULT_TIMEOUT_S",
    "NOTES_URL_SCHEME",
    "extract_apple_note",
    "html_to_markdown",
]
