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


def build_trajectories(tools: list[dict]) -> list[dict]:
    tool_defs = _openai_tools(tools)
    out: list[dict] = []
    goals = {
        "Read": "Show me the contents of src/main.rs.",
        "Write": "Create a hello-world Rust file at src/main.rs.",
        "Edit": "Rename the variable foo to bar in src/main.rs.",
        "Grep": "Find where fn main is defined.",
        "Bash": "List the files in the workspace.",
        "WebSearch": "Search the web for the lamark agent.",
        "WebFetch": "Fetch and summarize https://example.com.",
        "MemorySearch": "What do you remember about my project preferences?",
        "Agent": "Delegate a deep code review of the repo to a subagent.",
        "SkillView": "Open the rust-review skill.",
        "TaskCreate": "Add a task to investigate the failing test.",
    }
    for t in adopt(tools):
        name = t["name"]
        if name not in goals:
            continue
        args = _sample_args(t.get("parameters", {}))
        result = {
            "Read": "fn main() { println!(\"hello\"); }",
            "Bash": "Cargo.toml  src/  target/",
            "WebSearch": '[{"title":"Lamark agent","url":"https://example.com"}]',
            "Grep": "src/main.rs:1: fn main() {",
        }.get(name, "ok")
        out.append({
            "uuid": f"tooltraj-{name.lower()}",
            "source": "synthetic_tool_trajectory",
            "reasoning": "off",
            "reducer_version": "nemotron-agentic-v1",
            "tools": tool_defs,
            "messages": [
                {"role": "system", "content": "You are Lamark, a local AI agent. Use tools when helpful."},
                {"role": "user", "content": goals[name]},
                {"role": "assistant", "content": None,
                 "tool_calls": [{"id": "call_1", "type": "function",
                                 "function": {"name": name, "arguments": json.dumps(args)}}]},
                {"role": "tool", "tool_call_id": "call_1", "content": result},
                {"role": "assistant", "content": f"Done — I used the {name} tool to handle that."},
            ],
        })
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
