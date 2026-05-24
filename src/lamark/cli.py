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
    no_memory: bool = typer.Option(
        False,
        "--no-memory",
        help="Skip recall — don't inject any facts into the system prompt.",
    ),
    system: typing.Optional[str] = typer.Option(
        None,
        "--system",
        help="Custom persona / system prompt (overrides default).",
    ),
    max_tokens: int = typer.Option(
        512,
        "--max-tokens",
        help="Max tokens to generate.",
    ),
    k: int = typer.Option(
        20,
        "-k",
        "--memory-k",
        help="How many top-K facts to inject from memory.",
    ),
) -> None:
    """Send a message to Lamark — memory-injected, locally-routed.

    MVP single-turn (Phase 1a). Phase 1b adds cloud-anonymized fallback;
    Module 10 will layer Hermes underneath for multi-turn agent loop.
    """
    from lamark.archive import Archive
    from lamark.inference import InferenceRouter, OpenAIChatClient, RoutingContext
    from lamark.memory import MemoryStore, recall

    cfg = load_config()
    cfg.honcho_db_path.parent.mkdir(parents=True, exist_ok=True)
    archive = Archive.open(cfg.home / "archive")

    # Step 1 — recall relevant facts
    facts: list = []
    if not no_memory:
        with MemoryStore.open(cfg.honcho_db_path) as store:
            facts = recall(store, query=message, k=k)

    # Step 2 — assemble system prompt
    from lamark.identity import IDENTITY_PROMPT

    system_lines: list[str] = []
    if system:
        system_lines.append(system)
    else:
        system_lines.append(IDENTITY_PROMPT)
    if facts:
        system_lines.append("\n## What we know about the user")
        for f in facts:
            system_lines.append(f"- {f.text}")
    system_prompt = "\n".join(system_lines)

    # Step 3 — route + call
    router = InferenceRouter(
        primary_endpoint=cfg.inference.primary_endpoint,
        dense_endpoint=cfg.inference.dense_endpoint,
        primary_model=cfg.inference.primary_model,
        dense_model=cfg.inference.dense_model,
        primary_quantization=cfg.inference.primary_quantization,
        dense_quantization=cfg.inference.dense_quantization,
    )
    backend = router.choose(RoutingContext(prompt=message))
    client = OpenAIChatClient(endpoint=backend.endpoint, model=backend.model)

    try:
        result = client.chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message},
            ],
            max_tokens=max_tokens,
        )
    except RuntimeError as e:
        console.print(
            f"[red]✗ Inference failed[/red] (backend={backend.name} at {backend.endpoint}): {e}"
        )
        raise typer.Exit(1) from None

    # Step 4 — print + archive
    console.print(result.content)
    archive.write_pair(
        messages=[
            {"role": "user", "content": message},
            {"role": "assistant", "content": result.content},
        ],
        source="user_explicit",
        confidence=0.9,
        evidence_path="chat_turn",
    )


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


@app.command("seed-identity")
def seed_identity(
    regenerate: bool = typer.Option(
        False,
        "--regenerate",
        help="Wipe previous identity seeds and re-add (use after editing IDENTITY_PROMPT).",
    ),
) -> None:
    """Write Lamark identity Q&A pairs into the training archive.

    These pairs (~30 paraphrases of who-am-I, what-do-I-do, where-do-I-run,
    how-do-I-learn) become eligible for the nightly LoRA fine-tune. After
    enough cycles, the model "knows" its own identity without needing the
    full IDENTITY_PROMPT injected every time.
    """
    from lamark.archive import Archive
    from lamark.bootstrap.seed_identity import seed_identity_into_archive
    from lamark.memory import MemoryStore

    cfg = load_config()
    cfg.honcho_db_path.parent.mkdir(parents=True, exist_ok=True)
    archive = Archive.open(cfg.home / "archive")

    if regenerate:
        # Remove previous identity seeds before re-adding
        with MemoryStore.open(cfg.honcho_db_path) as store:
            removed = store.delete_fact_where(evidence_prefix="lamark-identity-seed")
        console.print(f"[dim]Cleared {removed} previous identity-seed Facts.[/dim]")

    with MemoryStore.open(cfg.honcho_db_path) as store:
        summary = seed_identity_into_archive(archive, store=store)

    console.print(
        f"[green]✓[/green] Identity seeded: "
        f"{summary['pairs_written']} Q&A pairs written to archive "
        f"with source={summary['source']!r} and evidence={summary['evidence_path']!r}"
    )
    console.print(
        "[dim]Next step: `lamark train --status` to see them as curatable, "
        "then `lamark train --now --no-threshold` to dispatch a fine-tune.[/dim]"
    )


@app.command("train")
def train(
    now: bool = typer.Option(False, "--now", help="Run the trainer immediately (instead of dry-run)."),
    plan_only: bool = typer.Option(
        False,
        "--plan",
        help="Print the curation plan without running anything.",
    ),
    status: bool = typer.Option(
        False,
        "--status",
        help="Print archive size + readiness summary, then exit.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Walk pipeline but do not invoke trainer (works with --now).",
    ),
    no_threshold: bool = typer.Option(
        False,
        "--no-threshold",
        help="Bypass the minimum-records guard (default refuses < 50 records).",
    ),
    min_confidence: float = typer.Option(
        0.7,
        "--min-confidence",
        help="Exclude pairs with confidence below this.",
    ),
    include_agent_edits: bool = typer.Option(
        False,
        "--include-agent-edits",
        help="Include source=agent_self_edit pairs (default: exclude — risk of feedback loops).",
    ),
    target_model: str = typer.Option(
        "Qwen/Qwen3.6-27B",
        "--target-model",
        help="Base model for the LoRA adapter (default: dense 27B for style).",
    ),
) -> None:
    """Force a LoRA fine-tune run on the accumulated archive.

    Plan A.5 ships the CLI surface + curation pipeline. The actual trainer
    (Unsloth + DoRA on Spark) is stubbed — Module 14 (Phase 2) wires it.
    """
    from lamark.archive import Archive
    from lamark.train import build_plan, count_archive, dispatch_training

    cfg = load_config()
    archive = Archive.open(cfg.home / "archive")
    archive_total = count_archive(archive)

    # --status: report and exit
    if status:
        if archive_total == 0:
            console.print("[yellow]Archive empty — Lamark not ready to train.[/yellow]")
            console.print(f"  archive path: {cfg.home / 'archive'}")
            return
        console.print(f"[green]Archive:[/green] {archive_total} records total")
        plan = build_plan(archive, min_confidence=min_confidence)
        console.print(f"  curatable (current filters): {len(plan)}")
        if len(plan) < 50:
            console.print(
                f"  [yellow]Below default threshold of 50 records — `lamark train --now` will refuse "
                "without `--no-threshold`.[/yellow]"
            )
        return

    # Build the plan
    allowed_sources = ("user_explicit", "bootstrap", "imported")
    if include_agent_edits:
        allowed_sources = (*allowed_sources, "agent_self_edit")
    plan = build_plan(archive, min_confidence=min_confidence, allowed_sources=allowed_sources)

    # --plan: print and exit, never invoke trainer
    if plan_only:
        console.print(f"[green]Curation plan[/green] (filters: {plan.filters_applied}):")
        console.print(f"  archive total:   {plan.archive_total}")
        console.print(f"  candidate pairs: {len(plan)}")
        console.print(f"  target model:    {target_model}")
        if not plan.records:
            console.print("  [dim]no records pass filters — would refuse to train[/dim]")
        return

    if not now:
        console.print(
            "[yellow]Use `lamark train --now` to actually run, "
            "`--plan` to inspect candidates, or `--status` for a summary.[/yellow]"
        )
        raise typer.Exit(2)

    # --now path
    if len(plan) < 50 and not no_threshold:
        console.print(
            f"[red]✗ Refusing: only {len(plan)} curatable records (need ≥ 50). "
            "Pass `--no-threshold` to override (training on too little data overfits).[/red]"
        )
        raise typer.Exit(2)

    console.print(
        f"[green]→ Dispatching trainer[/green] ({len(plan)} records, {target_model}, "
        f"dry_run={dry_run})..."
    )
    if dry_run:
        report = {
            "ok": True,
            "n_pairs": len(plan),
            "target_model": target_model,
            "dry_run": True,
            "notes": ["dry-run: dispatcher NOT called"],
        }
    else:
        report = dispatch_training(
            plan.records, dry_run=False, target_model=target_model
        )

    icon = "✓" if report.get("ok") else "✗"
    color = "green" if report.get("ok") else "red"
    console.print(f"[{color}]{icon}[/{color}] trainer report: n_pairs={report.get('n_pairs')}")
    for note in report.get("notes", []):
        console.print(f"  [dim]{note}[/dim]")


def main() -> None:
    """Entry point referenced from pyproject.toml [project.scripts]."""
    app()


if __name__ == "__main__":
    main()
