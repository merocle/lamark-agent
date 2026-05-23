"""
Lamark CLI entry point.

`lamark` is the user-facing command. Subcommands cover config, memory,
bootstrap, chat, eval. Phase 1 ships the skeleton; behaviour fills in
module-by-module per docs/phase-1-plan.md.
"""

from __future__ import annotations

import json
import sys
import typing
from pathlib import Path

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
    chatgpt: typing.Optional[Path] = typer.Option(
        None,
        "--chatgpt",
        help="Path to ChatGPT export conversations.json — imports historical user messages.",
        exists=True,
        dir_okay=False,
    ),
    obsidian: typing.Optional[Path] = typer.Option(
        None,
        "--obsidian",
        help="Path to an Obsidian vault directory — imports non-empty .md notes.",
        exists=True,
        file_okay=False,
        dir_okay=True,
    ),
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
    """Day-0 onboarding wizard — seed identity facts + (optionally) import history."""
    from lamark.archive import Archive
    from lamark.bootstrap import run_bootstrap
    from lamark.bootstrap.importers import import_chatgpt_export
    from lamark.memory import MemoryStore
    from lamark.redaction import SecretFound

    cfg = load_config()
    cfg.honcho_db_path.parent.mkdir(parents=True, exist_ok=True)
    # Phase 1a: training-data archive lives next to the SQLite db
    archive = Archive.open(cfg.home / "archive")

    with MemoryStore.open(cfg.honcho_db_path) as store:
        try:
            result = run_bootstrap(
                store=store,
                name=name,
                locale=locale,
                role=role,
                interactive=interactive,
                force=force,
                archive=archive,
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

        if chatgpt is not None:
            try:
                imp = import_chatgpt_export(store, chatgpt, archive=archive)
            except SecretFound as e:
                console.print(
                    f"[red]✗ ChatGPT import halted — verified secret detected ({e.category}). "
                    "No partial writes made.[/red]"
                )
                raise typer.Exit(1) from None
            console.print(
                f"[green]✓[/green] ChatGPT import: "
                f"+{imp.facts_added} facts from {imp.conversations_processed} conversations "
                f"({imp.messages_seen} user messages)"
            )

        if obsidian is not None:
            from lamark.bootstrap.importers import import_obsidian_vault

            try:
                obs_imp = import_obsidian_vault(store, obsidian, archive=archive)
            except SecretFound as e:
                console.print(
                    f"[red]✗ Obsidian import halted — verified secret detected ({e.category}). "
                    "No partial writes made.[/red]"
                )
                raise typer.Exit(1) from None
            console.print(
                f"[green]✓[/green] Obsidian import: "
                f"+{obs_imp.facts_added} facts from {obs_imp.files_processed} notes"
            )


def main() -> None:
    """Entry point referenced from pyproject.toml [project.scripts]."""
    app()


if __name__ == "__main__":
    main()
