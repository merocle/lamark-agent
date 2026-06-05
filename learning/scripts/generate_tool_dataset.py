#!/usr/bin/env python3
"""
Generate synthetic tool-knowledge + tool-call-trajectory training data from
learning/data/tools.yaml — the authoritative catalog.

Fixes three observed failures:
  (a) "the model confabulates about its own tools" — taught from the catalog what
      exists, plus NEGATIVE/typo refusals so a misspelled or unknown tool is
      declined, not invented;
  (b) "the model narrates `WebSearch(...)` instead of emitting a call" — taught
      with native `tool_calls[]` trajectories that present the real tools[] schema
      and show incoming `role:"tool"` results and outgoing tool calls;
  (c) "malformed <think>" — a controlled fraction of rows carry a well-formed
      reasoning block (via agentic_format.wrap_thinking).

All rows are emitted in the canonical schema (agentic_format) so the train/serve
format never drifts. Template-driven and deterministic (no API cost); args are
valid-by-construction against each tool's JSON-Schema, so the data never teaches
a malformed call.

Subcommands:
  facts         -> learning/data/tool_facts.jsonl              (facts schema; ROME subject-in-prompt)
  qa            -> learning/datasets/lamark/tool_qa.jsonl       (conversations; back-compat prose bucket)
  trajectories  -> learning/datasets/lamark/tool_trajectories.jsonl  (canonical agent trajectories)
  all           -> all three
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

import agentic_format as af

REPO = Path(__file__).resolve().parents[2]
TOOLS_YAML = REPO / "learning" / "data" / "tools.yaml"

_SYS = "You are Lamark, a local AI agent. Use tools when helpful; answer directly when not."


def load_tools() -> list[dict]:
    return yaml.safe_load(TOOLS_YAML.read_text(encoding="utf-8"))["tools"]


def adopt(tools: list[dict]) -> list[dict]:
    return [t for t in tools if t.get("status") == "adopt-v0.1" and t.get("name")]


# ── facts (ROME subject-in-prompt schema; unchanged shape) ────────────────────
def build_facts(tools: list[dict]) -> list[dict]:
    out: list[dict] = []
    for t in adopt(tools):
        name = t["name"]
        subject = f"Lamark's {name} tool"
        origin = t.get("hermes_origin")
        target = (f"is Lamark's name for the tool Hermes calls {origin}." if origin
                  else "is a Lamark-specific tool with no Hermes origin.")
        flag = ("read-only" if t.get("read_only")
                else "a destructive, permission-gated tool" if t.get("destructive")
                else "a state-changing tool")
        paraphrases = [
            f"What is Lamark's {name} tool?",
            f"What does the {name} tool do in Lamark?",
            f"Does Lamark have a {name} tool?",
        ]
        if origin:
            paraphrases.append(f"Which Hermes tool does Lamark's {name} correspond to?")
        out.append({
            "id": f"tool-{name.lower()}-maps",
            "edit": {"prompt": subject, "subject": subject, "target_new": target},
            "paraphrases": paraphrases,
            "neighborhood": [
                {"prompt": "Lamarck the biologist proposed", "expected_substring": "inherit"},
                {"prompt": "The capital of France is", "expected_substring": "Paris"},
            ],
            "tags": ["tooling", t.get("toolset", "")],
        })
        out.append({
            "id": f"tool-{name.lower()}-flag",
            "edit": {"prompt": subject, "subject": subject, "target_new": f"is {flag}."},
            "paraphrases": [f"Is Lamark's {name} tool read-only?",
                            f"Can Lamark's {name} tool change state?"],
            "neighborhood": [{"prompt": "Water boils at", "expected_substring": "100"}],
            "tags": ["tooling", "capability"],
        })
    return out


# ── qa (conversations; positives + negatives) ─────────────────────────────────
def build_qa(tools: list[dict]) -> list[dict]:
    out: list[dict] = []
    adopt_names = sorted(t["name"] for t in adopt(tools))
    for t in adopt(tools):
        name, origin, desc = t["name"], t.get("hermes_origin"), t.get("description", "")
        out.append(af.conversations_row(
            f"What does Lamark's {name} tool do?",
            desc + (f" It is Lamark's equivalent of Hermes' {origin}." if origin else " It is a Lamark-specific tool.")))
        out.append(af.conversations_row(f"Does Lamark have a {name} tool?", f"Yes. {desc}"))
        if origin:
            out.append(af.conversations_row(f"Which Hermes tool does Lamark's {name} map to?",
                                            f"Lamark's {name} maps to Hermes' {origin}."))
        for fact in t.get("facts", []):
            out.append(af.conversations_row(f"Tell me about Lamark's {name} tool.", fact))

    # Negatives — the cure for "tolls is an alias for schedule_cron"-style fabrication.
    # Typos, near-misses, and capabilities outside the catalog are DECLINED, not invented.
    for bogus in ["tolls", "tols", "filemanager", "sql", "screenshot", "email", "deploy", "git_push"]:
        out.append(af.conversations_row(
            f"Use your {bogus} tool.",
            f"I don't have a tool called `{bogus}`. My v0.1 tools are: {', '.join(adopt_names)}. "
            f"Tell me what you're trying to do and I'll pick the right one."))
    out.append(af.conversations_row(
        "list of tolls",
        f"Did you mean my tools? I have: {', '.join(adopt_names)}."))
    # Deferred / dropped capabilities — grounded in the catalog's defer/drop status.
    out.append(af.conversations_row(
        "Can Lamark browse the web with a headless browser?",
        "Not in v0.1 — the browser_* tools are deferred to v0.2. I can fetch URLs with WebFetch and search with WebSearch."))
    out.append(af.conversations_row(
        "Does Lamark send Telegram or Discord messages with a tool?",
        "No — messaging is handled by Lamark's gateway layer, not an agent tool. send_message/discord are dropped from the catalog."))
    out.append(af.conversations_row("What tools can you use?",
                                    "I have these v0.1 tools: " + ", ".join(adopt_names) + "."))
    out.append(af.conversations_row("What tools do you have?",
                                    "My v0.1 tools are: " + ", ".join(adopt_names) + "."))
    return out


# ── trajectory inventory (valid-by-construction args, real income/outgoing) ───
_FILES = ["src/main.rs", "src/lib.rs", "Cargo.toml", "README.md",
          "agent/crates/lamark-core/src/lib.rs", "learning/scripts/train_sft.py"]
_QUERIES = ["the lamark agent", "rust async runtimes", "tokio vs async-std performance",
            "qwen3.5 tool calling format", "MoE LoRA training on DGX Spark", "ripgrep flags"]
_SYMS = ["fn main", "struct ToolRegistry", "trait Tool", "impl ModelProvider", "async fn run_turn"]
_CMDS = ["ls -la", "git status", "cargo check", "just test -p lamark-core", "df -h"]
_URLS = ["https://example.com", "https://doc.rust-lang.org/book/", "https://tokio.rs"]
_NOTES = ["the user prefers tabs over spaces", "the project targets DGX Spark",
          "CI runs `just test`", "the user is Aleksei, a JetBrains engineer"]
_TASKS = ["investigate the failing login test", "add docs to the provider trait",
          "benchmark the tokenizer", "wire the trajectory bucket into training"]
_SKILLS = ["rust-review", "commit-helper", "test-writer"]


class _Builder:
    """Accumulates trajectories. Every ~3rd single-step row gets a reasoning block,
    so the model sees `<think>` paired with a real tool call (not bare prose)."""

    def __init__(self, defs: list[dict]):
        self.defs = defs
        self.rows: list[dict] = []
        self._n = 0

    def _cid(self) -> str:
        self._n += 1
        return f"call_{self._n}"

    def step(self, user: str, name: str, args: dict, result: str, final: str,
             *, reason: str | None = None) -> None:
        cid = self._cid()
        msgs = [
            af.system_msg(_SYS), af.user_msg(user),
            af.assistant_msg(reasoning=reason, tool_calls=[af.tool_call(cid, name, args)]),
            af.tool_result(cid, result),
            af.assistant_msg(final),
        ]
        self.rows.append(af.trajectory_row(
            msgs, tools=self.defs, reasoning="on" if reason else "off",
            source="tool-traj-single"))

    def two_step(self, user, n1, a1, r1, mid, n2, a2, r2, final, *, reason=None) -> None:
        c1, c2 = self._cid(), self._cid()
        msgs = [
            af.system_msg(_SYS), af.user_msg(user),
            af.assistant_msg(reasoning=reason, tool_calls=[af.tool_call(c1, n1, a1)]),
            af.tool_result(c1, r1),
            af.assistant_msg(mid, tool_calls=[af.tool_call(c2, n2, a2)]),
            af.tool_result(c2, r2),
            af.assistant_msg(final),
        ]
        self.rows.append(af.trajectory_row(msgs, tools=self.defs, reasoning="on" if reason else "off",
                                           source="tool-traj-multi"))

    def recover(self, user, name, args, error, retry_args, result, final) -> None:
        """Tool returns an error (incoming) -> assistant retries with fixed args
        (outgoing). Teaches error handling, not just happy-path calls."""
        c1, c2 = self._cid(), self._cid()
        msgs = [
            af.system_msg(_SYS), af.user_msg(user),
            af.assistant_msg(tool_calls=[af.tool_call(c1, name, args)]),
            af.tool_result(c1, error),
            af.assistant_msg(reasoning="The call failed; I'll correct the arguments and retry.",
                             tool_calls=[af.tool_call(c2, name, retry_args)]),
            af.tool_result(c2, result),
            af.assistant_msg(final),
        ]
        self.rows.append(af.trajectory_row(msgs, tools=self.defs, reasoning="on",
                                           source="tool-traj-recover"))

    def refuse(self, user: str, answer: str) -> None:
        """A trajectory where the right move is NO tool call — tools[] are present
        but the assistant declines/answers directly. Counter-teaches confabulation."""
        msgs = [af.system_msg(_SYS), af.user_msg(user),
                af.assistant_msg(answer, reasoning="No catalog tool fits this; I should answer directly.")]
        self.rows.append(af.trajectory_row(msgs, tools=self.defs, reasoning="on",
                                           source="tool-traj-refuse"))


def build_trajectories(tools: list[dict]) -> list[dict]:
    b = _Builder(af.tool_defs(tools))
    names = sorted(t["name"] for t in adopt(tools))

    # single-step, every 3rd with reasoning
    for i, f in enumerate(_FILES):
        b.step(f"Show me the contents of {f}.", "Read", {"file_path": f},
               'fn main() { println!("hi"); }', "That file defines the entry point.",
               reason=f"The user wants to see {f}; I'll read it." if i % 3 == 0 else None)
    for i, q in enumerate(_QUERIES):
        b.step(f"Search the web for {q}.", "WebSearch", {"query": q},
               f'[{{"title":"{q}","url":"https://example.com"}}]', f"Here are the top results for {q}.",
               reason="This needs current web data, so I'll search." if i % 3 == 0 else None)
    for i, s in enumerate(_SYMS):
        b.step(f"Find where {s} is defined.", "Grep", {"pattern": s},
               f"src/main.rs:1: {s}", f"{s} is defined in src/main.rs.",
               reason="A code search is the right tool here." if i % 3 == 0 else None)
    for i, c in enumerate(_CMDS):
        b.step(f"Run `{c}`.", "Bash", {"command": c}, "ok", "Done.",
               reason="The user asked me to run a shell command." if i % 3 == 0 else None)
    for u in _URLS:
        b.step(f"Fetch and summarize {u}.", "WebFetch", {"url": u}, "<page text>", "Summarized.")
    for q in _QUERIES[:4]:
        b.step(f"What do you remember about {q}?", "MemorySearch", {"query": q},
               "no prior notes", f"I have no stored notes on {q} yet.")
    for n in _NOTES:
        b.step(f"Remember that {n}.", "MemoryWrite", {"content": n}, "saved", "Noted.")
    for t in _TASKS:
        b.step(f"Add a task to {t}.", "TaskCreate", {"subject": t, "description": t},
               "task #1 created", "Task added.")
    for sk in _SKILLS:
        b.step(f"Open the {sk} skill.", "SkillView", {"skill_name": sk}, "# skill body", "Opened.")
    for ext, lang in [("**/*.rs", "Rust"), ("**/*.py", "Python"), ("**/*.toml", "TOML")]:
        b.step(f"List the {lang} files.", "Glob", {"pattern": ext}, "a.rs\nb.rs", "Listed.")

    # two-step combos
    for ext, lang in [("**/*.rs", "Rust"), ("**/*.toml", "TOML")]:
        f = "src/main.rs" if "rs" in ext else "Cargo.toml"
        b.two_step(f"Find the {lang} files, then read the first one.",
                   "Glob", {"pattern": ext}, f"{f}\nother", f"Found them; reading {f}.",
                   "Read", {"file_path": f}, "fn main() {}", f"The first {lang} file defines main.",
                   reason="First list, then read the first hit.")
    for s in _SYMS[:3]:
        b.two_step(f"Find {s} and show its file.", "Grep", {"pattern": s}, "src/main.rs:1",
                   "Located it; opening the file.", "Read", {"file_path": "src/main.rs"},
                   f"{s} ...", f"{s} lives in src/main.rs.")
    for c in ["ls", "git status"]:
        b.two_step(f"Run `{c}`, then read Cargo.toml.", "Bash", {"command": c}, "Cargo.toml\nsrc",
                   "Now reading the manifest.", "Read", {"file_path": "Cargo.toml"},
                   '[package]\nname="lamark"', "Here's the manifest.")

    # error-recovery (incoming error -> corrected outgoing retry)
    b.recover("Read the config file.", "Read", {"file_path": "/workspace/config.tom"},
              '{"error":"ENOENT: no such file /workspace/config.tom"}',
              {"file_path": "/workspace/config.toml"}, "[package]\nname=\"lamark\"",
              "I had a typo in the path; the config is the lamark package manifest.")
    b.recover("Search the repo for the turn loop.", "Grep", {"pattern": "run_turn("},
              '{"error":"regex parse error: unclosed group"}',
              {"pattern": "fn run_turn"}, "agent/crates/lamark-core/src/turn.rs:42: fn run_turn",
              "The first pattern was an invalid regex; run_turn is in turn.rs.")

    # refusals — tools present, but no tool fits, so answer directly (no fabricated call)
    b.refuse("What's 17 * 23?", "17 * 23 = 391.")
    b.refuse("Use your tolls tool to list files.",
             f"I don't have a `tolls` tool. To list files I'd use Glob. My tools are: {', '.join(names)}.")
    b.refuse("Send a Telegram message to my team.",
             "I can't — messaging is the gateway layer's job, not an agent tool. None of my tools send messages.")
    b.refuse("Explain what a LoRA adapter is.",
             "A LoRA adapter is a small set of low-rank weight deltas trained on top of a frozen base model; "
             "it adapts behavior cheaply without touching the base weights. No tool needed for that.")

    # capability roll-up as a trajectory (answered, not called)
    b.refuse("What tools do you have available right now?",
             "My v0.1 tools are: " + ", ".join(names) + ".")
    return b.rows


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate tool-knowledge + trajectory data from tools.yaml")
    ap.add_argument("mode", choices=["facts", "qa", "trajectories", "all"])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    tools = load_tools()
    data_dir = REPO / "learning" / "data"
    ds_dir = REPO / "learning" / "datasets" / "lamark"

    if args.mode in ("facts", "all"):
        af.write_jsonl(data_dir / "tool_facts.jsonl", build_facts(tools), dry_run=args.dry_run)
    if args.mode in ("qa", "all"):
        af.write_jsonl(ds_dir / "tool_qa.jsonl", build_qa(tools), dry_run=args.dry_run)
    if args.mode in ("trajectories", "all"):
        rows = af.validate_rows(build_trajectories(tools))
        af.write_jsonl(ds_dir / "tool_trajectories.jsonl", rows, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
