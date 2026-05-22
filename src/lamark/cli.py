"""
Lamark CLI entry point.

`lamark` is the user-facing command. Subcommands cover config, memory,
bootstrap, chat, eval. Phase 1 ships the skeleton; behaviour fills in
module-by-module per docs/phase-1-plan.md.
"""

from __future__ import annotations

import json
import sys

import typer
from rich.console import Console

from lamark import __version__
from lamark.config import load_config

console = Console()

app = typer.Typer(
    name="lamark",
    help="Lamark — locally-hosted personal AI agent that grows with you.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
)


def _version_callback(value: bool) -> None:
    if value:
        # Print to stdout so test capture works regardless of stderr/stdout split
        typer.echo(f"lamark {__version__}")
        raise typer.Exit(0)


@app.callback()
def _root(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """Lamark — locally-hosted personal AI agent that grows with you."""
    # Plumbing only; subcommands do the work.


# ---- config ---------------------------------------------------------------

config_app = typer.Typer(help="Inspect or modify Lamark configuration.", no_args_is_help=True)
app.add_typer(config_app, name="config")


@config_app.command("show")
def config_show() -> None:
    """Print the resolved configuration as JSON."""
    cfg = load_config()
    # Pydantic v2: serialize Path as str
    payload = json.loads(cfg.model_dump_json())
    typer.echo(json.dumps(payload, indent=2, sort_keys=True, default=str))


# ---- placeholders for upcoming subcommands -------------------------------

@app.command("chat")
def chat(
    message: str = typer.Argument(..., help="Message to send to Lamark."),
) -> None:
    """Send a single message (Phase 1: agent + memory + inference router not wired yet)."""
    console.print(
        "[yellow]chat[/yellow] subcommand is a Phase 1 placeholder — "
        "module 6 (inference router) and module 7 (Hermes fork) must land first."
    )
    sys.exit(2)


@app.command("memory")
def memory(action: str = typer.Argument("show", help="show | clear | export")) -> None:
    """Inspect or manage personal memory (Phase 1 module 4)."""
    console.print(
        f"[yellow]memory {action}[/yellow] is a Phase 1 placeholder — "
        "memory layer lands in module 2-4."
    )
    sys.exit(2)


@app.command("bootstrap")
def bootstrap() -> None:
    """Day-0 onboarding wizard (Phase 1 module 8)."""
    console.print(
        "[yellow]bootstrap[/yellow] is a Phase 1 placeholder — "
        "wizard lands in module 8 after memory layer is ready."
    )
    sys.exit(2)


def main() -> None:
    """Entry point referenced from pyproject.toml [project.scripts]."""
    app()


if __name__ == "__main__":
    main()
