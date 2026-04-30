"""Marginalia CLI — entry point for `marginalia` command."""

import typer
from rich.console import Console

app = typer.Typer(
    name="marginalia",
    help="Marginalia wiki engine CLI.",
    no_args_is_help=True,
)
console = Console()


@app.command()
def hello(name: str = typer.Argument("world")) -> None:
    """Smoke test: prove the CLI is wired up."""
    console.print(f"[bold green]Hello, {name}![/bold green] Marginalia engine is alive.")


@app.command()
def version() -> None:
    """Print the engine version."""
    from engine import __version__

    console.print(f"engine v{__version__}")


if __name__ == "__main__":
    app()
