"""Standalone FastMCP server exposing the local-fs inbox (design §8, §10G).

Runnable directly: ``uv run python -m engine.adapters.local_fs.mcp_server``.

Connects to any MCP client (Claude Code, Claude Desktop, Cursor, the
Claude Agent SDK via stdio transport). Exposes two tools targeting a
configurable root:

- ``list_files(subpath="")`` — list every file under the root.
- ``read_file(path, max_bytes=1_000_000)`` — read a file's text content,
  capped to keep tool responses bounded.

The root defaults to ``WIKI_RAW_PATH`` (per CLAUDE.md, ``~/wiki-raw/``).
Path-traversal protection blocks any access outside the root: agents
that read user-supplied paths must not be able to escape into the
filesystem.
"""

from __future__ import annotations

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

DEFAULT_ROOT = "~/wiki-raw/"
DEFAULT_MAX_BYTES = 1_000_000


def _resolve_root() -> Path:
    return Path(os.getenv("WIKI_RAW_PATH", DEFAULT_ROOT)).expanduser().resolve()


def _is_under_root(target: Path, root: Path) -> bool:
    try:
        target.relative_to(root)
    except ValueError:
        return False
    return True


def list_files_under(root: Path, subpath: str = "") -> list[str]:
    """Underlying logic for ``list_files``. Tested directly without MCP."""
    target = (root / subpath).resolve()
    if not _is_under_root(target, root):
        raise ValueError(f"path {subpath!r} escapes root {root}")
    if not target.exists():
        raise FileNotFoundError(f"{target} does not exist")
    if target.is_file():
        return [str(target.relative_to(root))]
    return sorted(str(p.relative_to(root)) for p in target.rglob("*") if p.is_file())


def read_file_under(
    root: Path,
    path: str,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> str:
    """Underlying logic for ``read_file``. Tested directly without MCP."""
    target = (root / path).resolve()
    if not _is_under_root(target, root):
        raise ValueError(f"path {path!r} escapes root {root}")
    if not target.exists():
        raise FileNotFoundError(f"{target} does not exist")
    if not target.is_file():
        raise ValueError(f"{target} is not a file")
    raw = target.read_bytes()
    if len(raw) > max_bytes:
        raw = raw[:max_bytes]
    return raw.decode("utf-8", errors="replace")


def build_server(root: Path | None = None) -> FastMCP:
    """Construct the FastMCP server. Factory so tests can pass a tmp_path root."""
    root_path = (root or _resolve_root()).resolve()
    server = FastMCP("marginalia-localfs")

    @server.tool()
    def list_files(subpath: str = "") -> list[str]:
        """List every file under the configured root, recursively.

        Pass ``subpath`` to scope the listing to a subdirectory.
        Returns paths relative to the root, sorted alphabetically.
        """
        return list_files_under(root_path, subpath)

    @server.tool()
    def read_file(path: str, max_bytes: int = DEFAULT_MAX_BYTES) -> str:
        """Read a file under the configured root as UTF-8 text.

        ``path`` is interpreted relative to the root. Files larger than
        ``max_bytes`` are truncated. Path traversal outside the root
        raises ``ValueError``.
        """
        return read_file_under(root_path, path, max_bytes=max_bytes)

    return server


def main() -> None:
    """Entry point for ``python -m engine.adapters.local_fs.mcp_server``."""
    server = build_server()
    server.run()


if __name__ == "__main__":
    main()


__all__ = [
    "DEFAULT_MAX_BYTES",
    "DEFAULT_ROOT",
    "build_server",
    "list_files_under",
    "main",
    "read_file_under",
]
