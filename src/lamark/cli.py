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
def bootstrap(
    name: str = typer.Option(None, "--name", help="Your name (skips prompt if set)."),
    locale: str = typer.Option(None, "--locale", help="Locale like en-US / ru-RU."),
    role: str = typer.Option(None, "--role", help="What you do (comma-separated facts)."),
    interactive: bool = typer.Option(
        False,
        "--interactive/--non-interactive",
        help="Prompt for any unspecified fields.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Wipe existing UserModel and re-bootstrap (destructive).",
    ),
) -> None:
    """Day-0 onboarding wizard — seed UserModel + initial facts."""
    from lamark.bootstrap import run_bootstrap
    from lamark.memory import MemoryStore

    cfg = load_config()
    cfg.honcho_db_path.parent.mkdir(parents=True, exist_ok=True)

    with MemoryStore.open(cfg.honcho_db_path) as store:
        try:
            result = run_bootstrap(
                store=store,
                name=name,
                locale=locale,
                role=role,
                interactive=interactive,
                force=force,
            )
        except RuntimeError as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(1) from None

    console.print(
        f"[green]✓[/green] Lamark bootstrapped. "
        f"facts_added={result.facts_added}, user_created={result.user_created}."
    )
    for note in result.notes:
        console.print(f"  [dim]{note}[/dim]")


def main() -> None:
    """Entry point referenced from pyproject.toml [project.scripts]."""
    app()


if __name__ == "__main__":
    main()
