"""CLI commands for the §7.6 three-layer cache.

Mounted at ``marginalia cache <verb>``:

    stats                        — per-layer entry counts, bytes, age range
    gc       --older-than <d>    — delete entries older than N days
    clear    [--yes]             — wipe L1 + L2 entirely (L3 is server-side)

Each command resolves the cache root in this order:
  --root  →  $MARGINALIA_CACHE_ROOT  →  $WIKI_CONTENT_REPO/.wiki/cache
  →  ./.wiki/cache (cwd fallback for the PoC)
"""

from __future__ import annotations

import os
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from engine.cache.admin import clear_all, gc, stats

app = typer.Typer(
    name="cache",
    help="Inspect and manage the §7.6 three-layer cache (L1 + L2).",
    no_args_is_help=True,
)
console = Console()


def _default_cache_root() -> Path:
    explicit = os.environ.get("MARGINALIA_CACHE_ROOT")
    if explicit:
        return Path(explicit)
    repo = os.environ.get("WIKI_CONTENT_REPO")
    if repo:
        return Path(repo) / ".wiki" / "cache"
    return Path.cwd() / ".wiki" / "cache"


def _format_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} {unit}"
        n /= 1024.0
    return f"{n:.1f} GB"


@app.command("stats")
def cache_stats(
    root: Path = typer.Option(  # noqa: B008
        None,
        "--root",
        help="Cache root directory. Default: $WIKI_CONTENT_REPO/.wiki/cache.",
    ),
) -> None:
    """Show on-disk cache statistics for L1 and L2."""
    root = root or _default_cache_root()
    s = stats(root)

    if s.total_entries == 0:
        console.print(f"[dim](cache empty at [cyan]{s.root}[/cyan])[/dim]")
        return

    table = Table(title=f"Cache stats — {s.root}")
    table.add_column("layer", style="cyan", no_wrap=True)
    table.add_column("entries", justify="right")
    table.add_column("size", justify="right")
    table.add_column("oldest")
    table.add_column("newest")

    for layer in s.layers:
        table.add_row(
            layer.layer,
            str(layer.entries),
            _format_bytes(layer.bytes),
            layer.oldest.strftime("%Y-%m-%d %H:%M") if layer.oldest else "-",
            layer.newest.strftime("%Y-%m-%d %H:%M") if layer.newest else "-",
        )
    console.print(table)
    console.print(f"[dim]total: {s.total_entries} entries, {_format_bytes(s.total_bytes)}[/dim]")


@app.command("gc")
def cache_gc(
    older_than: float = typer.Option(
        ...,
        "--older-than",
        help="Delete entries older than N days (float OK; 0 = drop everything before now).",
    ),
    root: Path = typer.Option(  # noqa: B008
        None,
        "--root",
        help="Cache root directory.",
    ),
) -> None:
    """Delete cache entries older than the given age."""
    root = root or _default_cache_root()
    n = gc(root, age_days=older_than)
    console.print(f"[green]Removed {n} cache entr{'y' if n == 1 else 'ies'}.[/green]")


@app.command("clear")
def cache_clear(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
    root: Path = typer.Option(  # noqa: B008
        None,
        "--root",
        help="Cache root directory.",
    ),
) -> None:
    """Wipe all L1 + L2 entries. L3 (Anthropic) expires server-side."""
    root = root or _default_cache_root()
    s = stats(root)
    if s.total_entries == 0:
        console.print("[dim](cache already empty)[/dim]")
        return
    if not yes:
        confirm = typer.confirm(f"Clear {s.total_entries} cache entries from {root}?")
        if not confirm:
            console.print("[yellow]aborted.[/yellow]")
            raise typer.Exit(code=1)
    n = clear_all(root)
    console.print(f"[green]Cleared {n} cache entr{'y' if n == 1 else 'ies'}.[/green]")


__all__ = ["app"]
