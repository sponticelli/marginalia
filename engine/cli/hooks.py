"""``marginalia hooks <verb>`` — debugging surface for shell hooks (design §14).

Today: one verb (``test``). Fires a hook with synthetic context so you
can confirm wiring before relying on a real ingest/lint to trip it.
Useful when you've just edited ``<wiki>/.wiki/config.toml`` or written
a new hook script and want to know whether it parses, executes, and
exits cleanly without waiting for a real job.

Future verbs (``list``, ``validate``) can mount here as needed.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console

from engine.cli.wikis import resolve_wiki_root
from engine.hooks.config import HOOK_EVENTS, load_hook_config
from engine.hooks.dispatcher import HookDispatcher

app = typer.Typer(
    name="hooks",
    help="Test and inspect lifecycle hooks (design §14).",
    no_args_is_help=True,
)
console = Console()


def _default_config_path() -> Path:
    """Resolve the active wiki's hook-config path."""
    return resolve_wiki_root() / ".wiki" / "config.toml"


def _synthetic_context(event: str) -> dict:
    """Return a context dict shaped like what production handlers fire.

    Mirrors the payloads in engine/jobs/dispatchers.py so a hook tested
    here behaves identically when a real ingest/lint job fires it. If
    you add new fields to the production payloads, mirror them here so
    hooks don't get a stale shape during testing.
    """
    if event == "on_ingest_complete":
        return {
            "job_id": "test-job-0001",
            "source_ref": "tests/synthetic/sample.md",
            "page_paths": ["sources/sample"],
            "status": "active",
            "confidence": "high",
            "tokens_in": 1000,
            "tokens_out": 200,
            "cost_usd": 0.0123,
            "duration_ms": 1234,
        }
    if event == "on_lint_complete":
        return {
            "job_id": "test-job-0002",
            "total_pages": 12,
            "contradictions": [],
            "stale_pages": [],
            "orphans": [],
            "tokens_in": 500,
            "tokens_out": 100,
            "cost_usd": 0.0089,
            "duration_ms": 4567,
        }
    raise typer.BadParameter(
        f"unknown event {event!r}; supported: {', '.join(HOOK_EVENTS)}",
    )


@app.command("test")
def test(
    event: str = typer.Option(
        ...,
        "--event",
        "-e",
        help=f"Hook event to fire. One of: {', '.join(HOOK_EVENTS)}.",
    ),
    config: Path = typer.Option(  # noqa: B008
        None,
        "--config",
        help="Hook config TOML. Default: $WIKI_CONTENT_REPO/.wiki/config.toml.",
    ),
) -> None:
    """Fire a hook with synthetic context; print stdout/stderr/exit code.

    Returns a non-zero exit if the hook itself fails (so this command
    is shell-pipeline-friendly: ``marginalia hooks test -e on_ingest_complete && ...``).
    """
    config_path = config or _default_config_path()
    cfg = load_hook_config(config_path)
    if cfg is None:
        console.print(f"[red]no hook config found at {config_path}[/red]")
        raise typer.Exit(code=1)

    hook = cfg.for_event(event)
    if hook is None:
        console.print(f"[yellow]no hook registered for {event!r} in {config_path}[/yellow]")
        raise typer.Exit(code=1)

    ctx = _synthetic_context(event)
    console.print(f"[dim]firing {event!r} → {hook.command} (timeout {hook.timeout_s}s)[/dim]")
    console.print("[dim]context:[/dim]")
    console.print_json(json.dumps(ctx))

    # No audit_writer here — this is a test/debug command, not a real
    # job. We let dispatch return its raw result so the user sees
    # exactly what happened without an audit-trail side effect.
    dispatcher = HookDispatcher(cfg)
    result = dispatcher.fire(event, ctx)
    if result is None:
        # Shouldn't happen — we already verified the hook is registered.
        console.print("[red]dispatcher returned no result (hook not registered?)[/red]")
        raise typer.Exit(code=1)

    console.print(f"\n[bold]exit_code:[/bold] {result.exit_code}")
    console.print(f"[bold]duration_ms:[/bold] {result.duration_ms}")
    console.print(f"[bold]timed_out:[/bold] {result.timed_out}")
    if result.stdout:
        console.print(f"[bold]stdout:[/bold]\n{result.stdout}")
    if result.stderr:
        console.print(f"[bold]stderr:[/bold]\n{result.stderr}")

    if not result.succeeded:
        raise typer.Exit(code=1)


__all__ = ["app"]
