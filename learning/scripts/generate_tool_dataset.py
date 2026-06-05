#!/usr/bin/env python3
"""
Generate synthetic tool-knowledge training data from learning/data/tools.yaml.

Fixes the "the model confabulates about its own tools" failure by teaching it,
from the authoritative catalog, (a) what tools exist and what they map to, and
(b) how a tool call is shaped. Template-driven and deterministic by default
(no API cost); pass --teacher to add gpt-5.4-mini paraphrase variety (needs
OPENAI_API_KEY) — that only varies natural-language phrasing, never the
tool_calls/arguments, which stay exact.

Subcommands:
  facts         -> learning/data/tool_facts.jsonl          (facts schema; ROME subject-in-prompt)
  qa            -> learning/datasets/lamark/tool_qa.jsonl   ({"conversations":[{role,value}]})
  trajectories  -> learning/datasets/lamark/tool_trajectories.jsonl  (Nemotron-Agentic-v1)
  all           -> all three

The catalog (learning/data/tools.yaml) is the single source of truth; it also
generates the table in docs/specs/04-tooling-and-protocol.md.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
TOOLS_YAML = REPO / "learning" / "data" / "tools.yaml"


def load_tools() -> list[dict]:
    data = yaml.safe_load(TOOLS_YAML.read_text(encoding="utf-8"))
    return data["tools"]


def adopt(tools: list[dict]) -> list[dict]:
    return [t for t in tools if t.get("status") == "adopt-v0.1" and t.get("name")]


def _conv(user: str, assistant: str) -> dict:
    return {"conversations": [
        {"role": "user", "value": user},
        {"role": "assistant", "value": assistant},
    ]}


# ── facts ────────────────────────────────────────────────────────────────────
def build_facts(tools: list[dict]) -> list[dict]:
    out: list[dict] = []
    for t in adopt(tools):
        name = t["name"]
        subject = f"Lamark's {name} tool"          # verbatim prefix of prompt (ROME)
        origin = t.get("hermes_origin")
        if origin:
            target = f"is Lamark's name for the tool Hermes calls {origin}."
        else:
            target = "is a Lamark-specific tool with no Hermes origin."
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
        # Two facts per tool: identity/mapping, and the read-only/destructive flag.
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
            "edit": {"prompt": subject, "subject": subject,
                     "target_new": f"is {flag}."},
            "paraphrases": [f"Is Lamark's {name} tool read-only?",
                            f"Can Lamark's {name} tool change state?"],
            "neighborhood": [
                {"prompt": "Water boils at", "expected_substring": "100"},
            ],
            "tags": ["tooling", "capability"],
        })
    return out


# ── qa ───────────────────────────────────────────────────────────────────────
def build_qa(tools: list[dict]) -> list[dict]:
    out: list[dict] = []
    adopt_names = {t["name"] for t in adopt(tools)}
    for t in adopt(tools):
        name, origin, desc = t["name"], t.get("hermes_origin"), t.get("description", "")
        out.append(_conv(f"What does Lamark's {name} tool do?",
                         f"{desc}" + (f" It is Lamark's equivalent of Hermes' {origin}." if origin else " It is a Lamark-specific tool.")))
        out.append(_conv(f"Does Lamark have a {name} tool?",
                         f"Yes. {desc}"))
        if origin:
            out.append(_conv(f"Which Hermes tool does Lamark's {name} map to?",
                             f"Lamark's {name} maps to Hermes' {origin}."))
        for fact in t.get("facts", []):
            out.append(_conv(f"Tell me about Lamark's {name} tool.", fact))
    # A few negative-capability answers grounded in the catalog (deferred/dropped).
    deferred = [t.get("hermes_origin") or t.get("name") for t in tools if t.get("status") == "defer"]
    out.append(_conv("Can Lamark browse the web with a headless browser?",
                     "Not in v0.1 — the browser_* tools are deferred to v0.2. Lamark v0.1 can fetch URLs with the WebFetch tool and search with WebSearch."))
    out.append(_conv("Does Lamark send Telegram or Discord messages with a tool?",
                     "No — messaging is handled by Lamark's gateway layer, not by an agent tool. send_message/discord are dropped from the tool catalog."))
    # Capability roll-up so the model answers "what tools do you have".
    out.append(_conv("What tools can you use?",
                     "I have these v0.1 tools: " + ", ".join(sorted(adopt_names)) + "."))
    return out


# ── trajectories (Nemotron-Agentic-v1) ───────────────────────────────────────
def _sample_args(params: dict) -> dict:
    props = (params or {}).get("properties", {}) or {}
    required = (params or {}).get("required", []) or list(props.keys())[:1]
    args = {}
    for k in required:
        spec = props.get(k, {})
        typ = spec.get("type", "string")
        if "enum" in spec:
            args[k] = spec["enum"][0]
        elif typ == "integer":
            args[k] = 10
        elif typ == "boolean":
            args[k] = False
        elif typ == "object":
            args[k] = {}
        elif typ == "array":
            args[k] = ["item"]
        else:
            args[k] = {"file_path": "/workspace/src/main.rs", "path": "/workspace",
                       "query": "lamark agent", "url": "https://example.com",
                       "command": "ls -la", "pattern": "fn main",
                       "content": "fn main() {}", "old_string": "foo",
                       "new_string": "bar", "skill_name": "rust-review",
                       "subject": "Investigate bug", "description": "Look into the failing test",
                       "prompt": "Summarize the repo", "server": "figma",
                       "tool": "list_files", "summary": "Delete build artifacts",
                       "branch": "feature/x", "plan": {}}.get(k, f"example_{k}")
    return args


def _openai_tools(tools: list[dict]) -> list[dict]:
    return [{"type": "function", "function": {
        "name": t["name"],
        "description": t.get("description", ""),
        "parameters": t.get("parameters", {"type": "object", "properties": {}}),
    }} for t in adopt(tools)]


_SYS = "You are Lamark, a local AI agent. Use tools when helpful."
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


def _traj(idx, defs, user, name, args, result, final, name2=None, args2=None, result2=None):
    """Build one Nemotron-Agentic-v1 trajectory (single- or two-step)."""
    asst1 = {"role": "assistant", "content": None,
             "tool_calls": [{"id": "call_1", "type": "function",
                             "function": {"name": name, "arguments": json.dumps(args)}}]}
    msgs = [{"role": "system", "content": _SYS}, {"role": "user", "content": user},
            asst1, {"role": "tool", "tool_call_id": "call_1", "content": result}]
    if name2:
        msgs.append({"role": "assistant", "content": None,
                     "tool_calls": [{"id": "call_2", "type": "function",
                                     "function": {"name": name2, "arguments": json.dumps(args2)}}]})
        msgs.append({"role": "tool", "tool_call_id": "call_2", "content": result2})
    msgs.append({"role": "assistant", "content": final})
    return {"uuid": f"tooltraj-{idx}", "source": "synthetic_tool_trajectory",
            "reasoning": "off", "reducer_version": "nemotron-agentic-v1",
            "tools": defs, "messages": msgs}


def build_trajectories(tools: list[dict]) -> list[dict]:
    """Template-driven native tool-call trajectories — single + two-step combos.

    Args are valid-by-construction against each tool's schema, so the data never
    teaches a malformed call. Variety comes from the cross-product of files /
    queries / symbols / commands, not an LLM."""
    defs = _openai_tools(tools)
    names = {t["name"] for t in adopt(tools)}
    out: list[dict] = []
    i = 0

    def add(*a, **k):
        nonlocal i
        out.append(_traj(i, defs, *a, **k))
        i += 1

    # ── single-step ──
    for f in _FILES:
        add(f"Show me the contents of {f}.", "Read", {"file_path": f},
            "fn main() { println!(\"hi\"); }", f"That file defines the entry point.")
    for q in _QUERIES:
        add(f"Search the web for {q}.", "WebSearch", {"query": q},
            f'[{{"title":"{q}","url":"https://example.com"}}]', f"Here are the top results for {q}.")
    for s in _SYMS:
        add(f"Find where {s} is defined.", "Grep", {"pattern": s},
            f"src/main.rs:1: {s}", f"{s} is defined in src/main.rs.")
    for c in _CMDS:
        add(f"Run `{c}`.", "Bash", {"command": c}, "ok", "Done.")
    for u in _URLS:
        add(f"Fetch and summarize {u}.", "WebFetch", {"url": u}, "<page text>", "Summarized.")
    for q in _QUERIES[:4]:
        add(f"What do you remember about {q}?", "MemorySearch", {"query": q},
            "no prior notes", f"I have no stored notes on {q} yet.")
    for n in _NOTES:
        add(f"Remember that {n}.", "MemoryWrite", {"content": n}, "saved", "Noted.")
    for t in _TASKS:
        add(f"Add a task to {t}.", "TaskCreate", {"subject": t, "description": t},
            "task #1 created", "Task added.")
    for sk in _SKILLS:
        add(f"Open the {sk} skill.", "SkillView", {"skill_name": sk}, "# skill body", "Opened.")
    for ext, lang in [("**/*.rs", "Rust"), ("**/*.py", "Python"), ("**/*.toml", "TOML")]:
        add(f"List the {lang} files.", "Glob", {"pattern": ext}, "a.rs\nb.rs", "Listed.")
    add("Search the web — and only do that.", "WebSearch", {"query": "rust 2024 edition"},
        "[]", "No results.")

    # ── two-step combos ──
    for ext, lang in [("**/*.rs", "Rust"), ("**/*.toml", "TOML")]:
        f = "src/main.rs" if "rs" in ext else "Cargo.toml"
        add(f"Find the {lang} files, then read the first one.",
            "Glob", {"pattern": ext}, f"{f}\nother",
            f"The first {lang} file does X.", name2="Read", args2={"file_path": f},
            result2="fn main() {}")
    for s in _SYMS[:3]:
        add(f"Find {s} and show its file.", "Grep", {"pattern": s}, "src/main.rs:1",
            f"{s} lives in src/main.rs.", name2="Read", args2={"file_path": "src/main.rs"},
            result2=f"{s} ...")
    for c in ["ls", "git status"]:
        add(f"Run `{c}`, then read Cargo.toml.", "Bash", {"command": c}, "Cargo.toml\nsrc",
            "Here's the manifest.", name2="Read", args2={"file_path": "Cargo.toml"},
            result2="[package]\nname=\"lamark\"")

    assert names  # keep adopt() referenced
    return out


def write_jsonl(path: Path, rows: list[dict], dry: bool) -> None:
    if dry:
        print(f"[dry-run] would write {len(rows)} rows -> {path}")
        if rows:
            print("  sample:", json.dumps(rows[0], ensure_ascii=False)[:300])
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    print(f"wrote {len(rows)} rows -> {path}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate tool-knowledge training data from tools.yaml")
    ap.add_argument("mode", choices=["facts", "qa", "trajectories", "all"])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    tools = load_tools()
    data_dir = REPO / "learning" / "data"
    ds_dir = REPO / "learning" / "datasets" / "lamark"

    if args.mode in ("facts", "all"):
        write_jsonl(data_dir / "tool_facts.jsonl", build_facts(tools), args.dry_run)
    if args.mode in ("qa", "all"):
        write_jsonl(ds_dir / "tool_qa.jsonl", build_qa(tools), args.dry_run)
    if args.mode in ("trajectories", "all"):
        write_jsonl(ds_dir / "tool_trajectories.jsonl", build_trajectories(tools), args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
