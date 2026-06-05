#!/usr/bin/env python3
"""
Teacher generator for Lamark *full-fix* trajectories - long, end-to-end task runs
that mirror lambda/hermes-agent-reasoning-traces (glm-5.1) and the SWE/agentic
trajectory datasets in docs/datasets.md. Where generate_agentic_data.py emits
SHORT stylized snippets (1-4 calls), this emits a COMPLETE coding session: explore
-> reason -> edit -> verify -> summarize, over many iterations, with realistic
failures the agent recovers from, an iteration BUDGET, and a forced final summary.

Provenance: the shape is modeled on Nous Research's Hermes Agent reasoning traces
(MIT; see learning/vendor/hermes/ and docs/datasets.md §1A). Lamark re-themes the
system prompt and tool names (invariant 2) and emits the model-NEUTRAL canonical
format (agentic_format), so the output drops straight into build_dataset.py via
--fullfix-trajectories and trains any base model through model_template.

DIVISION OF LABOUR (valid-by-construction):
  The teacher supplies only CONTENT - the task, per-step reasoning, tool name+args,
  realistic tool results, and a final summary. THIS SCRIPT owns the wire format and
  the harness mechanics the teacher must never fake:
    * tool_call ids + matching role:"tool" results
    * arg validation against each tool's JSON-Schema (drops malformed steps/rows)
    * the iteration BUDGET - [BUDGET: Iteration k/M. j left ...] user turns injected
      deterministically near the limit
    * the forced final-summary turn when outcome="max_iterations"
    * af.validate_row() on every emitted trajectory
So a confused teacher can never inject a malformed call, an orphan tool result, or a
fake budget line into training.

FULL MIX (the requested flavor): each call asks for a spread of outcomes -
  solved_clean  - finishes well under budget; no budget warnings.
  solved_late   - finishes in the last few iterations; budget warnings appear.
  recovered     - hits failures (bad path/arg/test) mid-run, retries, still solves.
  max_iterations- runs out of budget WITHOUT finishing; ends in a forced honest
                  summary of partial progress + concrete next steps.

Never greedy (invariant 9). Shares the LiteLLM client + tool catalog with
generate_agentic_data.py (no duplication). Run where the proxy (LITELLM_BASE_URL,
default http://10.212.212.1:4000/v1) is reachable.

  python generate_fullfix_data.py --rounds 3 --per-call 2 \
      --out ~/.lamark/data/fullfix_trajectories.jsonl
  python generate_fullfix_data.py --dry-run     # print the prompt/plan, no calls
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import agentic_format as af
import generate_agentic_data as ag  # shared LiteLLM client + tool catalog loaders

# Lamark agent system prompt for full-fix trajectories. Adapted from the upstream
# Hermes execution-discipline + tool-use-enforcement prompts (MIT), re-themed to
# Lamark and English-only (invariants 2, 7). Tools are presented via the top-level
# tools[] array (canonical format), NOT inlined here.
LAMARK_SYSTEM = (
    "You are Lamark, a self-improving local AI agent written in Rust that runs on the "
    "user's own hardware. You complete software tasks end-to-end using your tools.\n"
    "\n"
    "Execution discipline:\n"
    "- Take action with tools; never describe what you would do without doing it.\n"
    "- Gather context first (read files, search) before editing; never guess file contents.\n"
    "- After a change, verify it (run the build, tests, or the program) before claiming success.\n"
    "- If a tool returns an error or empty result, diagnose and retry with corrected args.\n"
    "- Keep working until the task is complete and verified, or you are out of iteration budget.\n"
    "- When you finish (or run out of budget), give a concrete summary: what changed "
    "(file paths, commands, results), what remains, and the next steps."
)

# Budget injection - faithful to the dataset's observed `[BUDGET: ...]` user turns
# and the upstream final-summary guidance ("Be CONCRETE - include file paths, command
# outputs, error messages, line numbers"). Owned by the script, never the teacher.
WARN_AT = 4  # start warning when this many iterations remain


def _budget_warning(k: int, m: int) -> str:
    left = m - k
    return (f"[BUDGET: Iteration {k}/{m}. {left} iteration{'s' if left != 1 else ''} left. "
            f"Prioritize finishing the task - stop exploring and finalize.]")


def _forced_summary(m: int) -> str:
    return (f"[BUDGET: Iteration limit reached ({m}/{m}). No more tool calls will be executed. "
            f"Summarize what you accomplished, what remains unfinished, and the concrete next "
            f"steps - be specific with file paths, commands, error messages, and line numbers.]")


OUTCOMES = {
    "solved_clean": "the task is fully solved comfortably WITHIN budget (use ~6-9 steps). "
                    "No failed steps.",
    "solved_late": "the task is solved but only in the LAST few iterations (use ~10-13 steps). "
                   "It may include one recovered failure.",
    "recovered": "the agent hits 1-2 realistic FAILURES mid-run (wrong path, bad arg, failing "
                 "test, patch that doesn't apply), diagnoses each from the error result, retries "
                 "with corrected args, and ultimately SOLVES the task (use ~8-12 steps). Set "
                 "\"error\": true on each failed step.",
    "max_iterations": "the agent makes real progress but RUNS OUT of budget without finishing "
                      "(use ~12-15 steps, the last one mid-task). It may include a recovered "
                      "failure. 'final' is an HONEST summary of partial progress + next steps.",
}

# Realistic full-task framings, rotated so the corpus isn't all one kind of task.
FOCI = [
    "fix a failing test or a reported bug in an existing Python/Rust/JS module",
    "implement a small feature end-to-end (new function + wiring + a test)",
    "write and run a script that processes data (CSV/JSON/logs) and report results",
    "refactor a module for clarity or performance, keeping tests green",
    "investigate a runtime error from a stack trace and patch the root cause",
    "set up or fix a build/CI step (deps, config, lint) until it passes",
    "add input validation / error handling to a function and verify edge cases",
    "trace a value through several files to find where it goes wrong, then fix it",
]


def author_prompt(catalog: str, focus: str, n: int, budget: int) -> list[dict]:
    schema = (
        '{"trajectories": [ {'
        '"task": str, '
        '"category": str, "subcategory": str, '
        '"outcome": "solved_clean"|"solved_late"|"recovered"|"max_iterations", '
        '"steps": [ {"think": str, "tool": str (a catalog name), '
        '"args": object (matching that tool\'s params), '
        '"result": str (realistic tool output; an error string for failed steps), '
        '"error": bool (optional; true only for failed steps) } ], '
        '"final": str } ] }'
    )
    instr = (
        f"Generate {n} DIVERSE, realistic FULL-TASK agent trajectories for Lamark, a local "
        f"coding agent. Each trajectory is a COMPLETE multi-step session that solves (or runs "
        f"out of budget on) one concrete software task - like a real terminal/coding session, "
        f"not a one-shot answer.\n\n"
        f"Spread the {n} trajectories across DIFFERENT outcomes:\n"
        + "\n".join(f"  - {k}: {v}" for k, v in OUTCOMES.items()) + "\n\n"
        f"Lean the tasks toward: {focus}. The iteration budget is {budget}.\n\n"
        f"TOOLS YOU MAY USE (pick only these exact names; args must match; '*' = required):\n"
        f"{catalog}\n\n"
        f"Rules:\n"
        f"- Tasks must be things a developer asks a local coding agent. Vary domain and phrasing.\n"
        f"- A trajectory is a SEQUENCE of steps. Each step = one tool call + the result it returns.\n"
        f"  Typical arc: explore (read/search) -> reason -> edit (write/patch) -> verify (run "
        f"tests/build) -> finalize. Use SEVERAL steps; this is the whole point.\n"
        f"- Use ONLY the tool names above. Provide all required args with plausible, specific "
        f"values (real-looking file paths, commands, code).\n"
        f"- 'result' is what the tool would actually return: short and realistic. For failed "
        f"steps make it a believable error (traceback line, non-zero exit, 'No such file', a "
        f"patch-doesn't-apply message) and set \"error\": true.\n"
        f"- Every step's 'think' is concise first-person reasoning about why THAT tool/args now.\n"
        f"- 'final' is Lamark's closing message to the user. Be CONCRETE: name the files changed, "
        f"commands run, and results. For 'max_iterations', be honest that it's unfinished and "
        f"list next steps. Do NOT write tool calls as prose (no `Read(...)`) in 'final'.\n"
        f"- Do NOT include any budget/iteration lines yourself - those are added automatically.\n"
        f"- Return STRICT JSON, exactly this schema, no prose:\n{schema}"
    )
    return [{"role": "system", "content": "You produce strict-JSON synthetic agent training data."},
            {"role": "user", "content": instr}]


def spec_to_row(spec: dict, defs: list[dict], schemas: dict[str, dict],
                budget: int) -> dict | None:
    """Assemble one full-fix trajectory in canonical format, injecting the budget
    warnings and (for max_iterations) the forced summary. Returns None if any step
    references an unknown tool or omits a required arg."""
    task = (spec.get("task") or spec.get("user") or "").strip()
    final = (spec.get("final") or "").strip()
    steps = (spec.get("steps") or [])[:budget]  # never exceed the iteration budget
    if not task or not final or not steps:
        return None
    outcome = spec.get("outcome")
    if outcome not in OUTCOMES:
        outcome = "solved_clean"

    # Iteration budget M is the fixed cap so "k/M" and "j left" are exact. A
    # max_iterations run is one that consumed every iteration: M == #steps. A solved
    # run finishes with the cap above it, so the warnings only appear if it ran late.
    eff_steps = steps
    m = len(eff_steps) if outcome == "max_iterations" else budget

    msgs = [af.system_msg(LAMARK_SYSTEM), af.user_msg(task)]
    reasoning = "off"
    cid = 0
    for i, st in enumerate(eff_steps, start=1):
        tool = st.get("tool")
        if tool not in schemas:
            return None  # hallucinated tool name - drop the whole trajectory
        args = st.get("args") or {}
        if not isinstance(args, dict):
            return None
        if not all(k in args for k in (schemas[tool].get("required") or [])):
            return None
        # Inject the budget warning for the final WARN_AT iterations.
        if (m - i) < WARN_AT:
            msgs.append(af.user_msg(_budget_warning(i, m)))
        think = (st.get("think") or "").strip() or None
        if think:
            reasoning = "on"
        cid += 1
        call_id = f"call_{cid}"
        msgs.append(af.assistant_msg(reasoning=think, tool_calls=[af.tool_call(call_id, tool, args)]))
        result = str(st.get("result") or "").strip() or "ok"
        msgs.append(af.tool_result(call_id, result))

    if outcome == "max_iterations":
        msgs.append(af.user_msg(_forced_summary(m)))
    msgs.append(af.assistant_msg(final))
    return af.trajectory_row(msgs, tools=defs, reasoning=reasoning, source=f"litellm-fullfix-{outcome}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Teacher-model full-fix trajectory generator (LiteLLM)")
    ap.add_argument("--rounds", type=int, default=3, help="teacher calls (focus rotates each round)")
    ap.add_argument("--per-call", type=int, default=2, help="trajectories requested per call")
    ap.add_argument("--budget", type=int, default=15, help="iteration budget per trajectory")
    ap.add_argument("--out", type=Path,
                    default=ag.REPO / "learning" / "datasets" / "lamark" / "fullfix_trajectories.jsonl")
    ap.add_argument("--temperature", type=float, default=0.7)
    ap.add_argument("--top-p", type=float, default=0.9)
    ap.add_argument("--top-k", type=int, default=20)
    ap.add_argument("--max-tokens", type=int, default=8192)
    ap.add_argument("--list-models", action="store_true", help="print the proxy's /v1/models and exit")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.list_models:
        try:
            print("\n".join(ag.list_models()))
        except Exception as e:
            raise SystemExit(f"/v1/models failed: {e}")
        return 0

    tools = ag.load_adopt_tools()
    defs = af.tool_defs(tools)
    schemas = {t["name"]: (t.get("parameters") or {}) for t in tools}
    catalog = ag.catalog_text(tools)

    print(f"[fullfix] endpoint={ag.BASE_URL} model={ag.MODEL} rounds={args.rounds} "
          f"per_call={args.per_call} budget={args.budget} -> up to "
          f"{args.rounds * args.per_call} trajectories")

    if args.dry_run:
        print(f"[dry-run] sample prompt (focus={FOCI[0]!r}):\n")
        print(author_prompt(catalog, FOCI[0], args.per_call, args.budget)[1]["content"][:1800])
        print("\n[dry-run] no API calls made.")
        return 0

    ag.MODEL = ag.resolve_model(ag.MODEL)  # use a name the proxy actually serves

    rows: list[dict] = []
    kept = dropped = 0
    for r in range(args.rounds):
        focus = FOCI[r % len(FOCI)]
        raw = ag.chat(author_prompt(catalog, focus, args.per_call, args.budget),
                      temperature=args.temperature, top_p=args.top_p, top_k=args.top_k,
                      max_tokens=args.max_tokens)
        spec = ag.parse_json(raw)
        trajs = (spec or {}).get("trajectories", []) if isinstance(spec, dict) else []
        for tj in trajs:
            try:
                row = spec_to_row(tj, defs, schemas, args.budget)
                if row is None:
                    dropped += 1
                    continue
                af.validate_row(row)
            except (af.FormatError, KeyError, TypeError, ValueError):
                dropped += 1
                continue
            rows.append(row)
            kept += 1
        print(f"  round {r + 1}/{args.rounds} focus={focus[:40]!r}: +{len(trajs)} parsed "
              f"(kept {kept}, dropped {dropped})")

    af.write_jsonl(args.out, rows)
    print(f"[fullfix] done: {kept} kept, {dropped} dropped -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
