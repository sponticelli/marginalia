"""``marginalia use`` and ``marginalia wikis <verb>`` — multi-wiki UX (Phase 4 §4.2).

Thin Typer surface over ``engine/cli/wikis.py``. Kept in its own
module so the resolver library stays Typer-free (importable from
hooks, tests, dispatchers without dragging the CLI framework in).

Verb shape mirrors `git remote` / `kubectl config`: one verb pulls the
common operation (``use`` flips active) out as a top-level shortcut;
the rest live under ``marginalia wikis ...``.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from engine.cli.wikis import (
    WikiRegistryError,
    add_wiki,
    config_path,
    load_registry,
    remove_wiki,
    set_active,
)

app = typer.Typer(
    name="wikis",
    help="Manage the multi-wiki registry (~/.config/marginalia/wikis.toml).",
    no_args_is_help=True,
)
console = Console()


# ─── `marginalia wikis add` ──────────────────────────────────────────


@app.command("add")
def wikis_add(
    name: str = typer.Argument(..., help="Short name for this wiki (e.g., 'personal', 'work')."),
    root: Path = typer.Argument(  # noqa: B008
        ..., help="Path to the wiki content repo (the directory holding entities/, knowledge/, …)."
    ),
    raw_path: Path | None = typer.Option(  # noqa: B008
        None,
        "--raw-path",
        help="Per-wiki staging inbox override. Default: use $WIKI_RAW_PATH or ~/wiki-raw.",
    ),
) -> None:
    """Register a wiki.

    The first wiki added auto-becomes active so single-wiki users get
    the multi-wiki commands working immediately. Subsequent adds keep
    whatever ``active`` already pointed at.
    """
    try:
        reg = add_wiki(name, root, raw_path=raw_path)
    except WikiRegistryError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    msg = f"[green]registered[/green] {name} → {root}"
    if reg.active == name:
        msg += "  [dim](now active)[/dim]"
    console.print(msg)


# ─── `marginalia wikis remove` ───────────────────────────────────────


@app.command("remove")
def wikis_remove(
    name: str = typer.Argument(..., help="Wiki name to drop from the registry."),
) -> None:
    """Drop a wiki from the registry. Files on disk are untouched.

    If the removed wiki was active, ``active`` clears — the next CLI
    call will fall through to ``$WIKI_CONTENT_REPO`` or ``cwd``. The
    user is told so they can pick a replacement with ``marginalia use``.
    """
    try:
        reg = remove_wiki(name)
    except WikiRegistryError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]removed[/green] {name}")
    if reg.active is None and reg.wikis:
        console.print(
            "[yellow]no wiki is currently active.[/yellow] "
            f"Pick one with: marginalia use <{', '.join(sorted(reg.wikis))}>"
        )


# ─── `marginalia wikis list` ─────────────────────────────────────────


@app.command("list")
def wikis_list() -> None:
    """Pretty-print the registry. Active wiki is starred."""
    reg = load_registry()
    if not reg.wikis:
        console.print(
            f"[dim](no wikis registered; config path: {config_path()})[/dim]\n"
            "Add one with: [cyan]marginalia wikis add <name> <path>[/cyan]"
        )
        return

    table = Table(title=f"Wikis ({len(reg.wikis)} registered)", show_lines=False)
    table.add_column("", width=2)  # Active marker.
    table.add_column("name", style="cyan")
    table.add_column("root", overflow="fold")
    table.add_column("raw_path", overflow="fold")

    for name in sorted(reg.wikis):
        entry = reg.wikis[name]
        marker = "★" if reg.active == name else " "
        raw = str(entry.raw_path) if entry.raw_path else "[dim]—[/dim]"
        table.add_row(marker, name, str(entry.root), raw)
    console.print(table)


# ─── `marginalia use` (top-level shortcut) ───────────────────────────


def use(
    name: str = typer.Argument(..., help="Registered wiki name to activate."),
) -> None:
    """Switch the active wiki. Subsequent commands target this one.

    Equivalent to a hand-edit of the ``active`` field in
    ``~/.config/marginalia/wikis.toml``. The choice persists across
    shells; per-shell override available via ``$MARGINALIA_WIKI``.
    """
    try:
        reg = set_active(name)
    except WikiRegistryError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]using[/green] {name} → {reg.entry(name).root}")


__all__ = ["app", "use"]
