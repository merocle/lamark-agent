# Generating full-fix trajectories

How Lamark synthesizes **full-fix** training trajectories — complete, multi-iteration
agent sessions that solve (or run out of budget on) one concrete software task, the
same shape as [`lambda/hermes-agent-reasoning-traces`](https://huggingface.co/datasets/lambda/hermes-agent-reasoning-traces)
(glm-5.1 split) and the SWE / agentic-trajectory datasets catalogued in
[`docs/datasets.md`](../datasets.md).

The short, stylized trajectories from `generate_agentic_data.py` (1–4 tool calls:
single / multi / reasoning / refusal / recover) teach the *call format*. Full-fix
trajectories teach the *loop*: explore → reason → edit → verify → summarize, across
many iterations, with realistic failures, an iteration budget, and a forced final
summary. Both feed the same unified dataset.

> **Provenance.** The shape is modeled on Nous Research's Hermes Agent reasoning
> traces (MIT; vendored at `learning/vendor/hermes/`). Lamark re-themes the system
> prompt and tool names (invariant 2 — never call the product anything but Lamark)
> and emits the model-neutral canonical format, so one corpus trains any base model.

---

## What a full-fix trajectory looks like

The upstream `glm-5.1` rows are `{id, conversations, tools, category, subcategory, task}`,
where `conversations` cycles `system` (with a `<tools>` block) → `human` (the task) →
`gpt` (`<think>` + `<tool_call>`) → `tool` (`<tool_response>`), over 10–15+ turns,
including real failures (wrong cwd, failing tests), `[BUDGET: …]` iteration warnings,
and a forced summary when the budget runs out.

Lamark stores the **same content** in the canonical format
([`learning/scripts/agentic_format.py`](../../learning/scripts/agentic_format.py)) so it
trains every model through `model_template.TemplateAdapter`:

| Upstream (hermes/glm-5.1) | Lamark canonical |
|---|---|
| `conversations[].from/value` | `messages[].role/content` |
| `<tools>` block in the system turn | top-level `tools[]` array (OpenAI/Hermes function defs) |
| `gpt` turn `<think>…</think>` | assistant `thinking` field (neutral; rendered per-family at train time) |
| `gpt` turn `<tool_call>{…}</tool_call>` | assistant `tool_calls[]` (native, valid JSON args) |
| `tool` turn `<tool_response>…` | `role:"tool"` message keyed by `tool_call_id` |
| `[BUDGET: …]` injection | a `user` turn (see [Budget mechanics](#budget-mechanics)) |
| `task`, `category`, `subcategory` | task → first `user` turn; outcome encoded in `source` |

### Tool mapping

The dataset's 7 coding tools map onto Lamark's `adopt-v0.1` catalog
([`learning/data/tools.yaml`](../../learning/data/tools.yaml)):

| hermes/glm-5.1 | Lamark |
|---|---|
| `read_file` | `Read` |
| `write_file` | `Write` |
| `patch` | `Edit` |
| `search_files` | `Glob` (by name) + `Grep` (by content) |
| `terminal` | `Bash` |
| `process` | `Bash` (background/poll) |

Tasks land in the **Terminal & Coding** class; research tasks may also use
`WebSearch` / `WebFetch`.

---

## The four outcomes (full mix, incl. failures)

Every teacher call asks for a spread across all four so the model sees success and
graceful give-up, not just the happy path:

| `outcome` | What it teaches |
|---|---|
| `solved_clean` | Finishes comfortably within budget (~6–9 steps). No failed steps, no budget warnings. |
| `solved_late` | Solved only in the last few iterations (~10–13 steps). Budget warnings appear. |
| `recovered` | Hits 1–2 realistic failures mid-run (wrong path, bad arg, failing test, patch won't apply), diagnoses from the error result, retries with corrected args, and still solves (~8–12 steps). |
| `max_iterations` | Real progress but **runs out of budget without finishing** (~12–15 steps). Ends in a forced, honest summary of partial progress + concrete next steps. |

The outcome is recorded in the row `source` (`litellm-fullfix-<outcome>`) so the
blend report breaks coverage down per outcome.

---

## Division of labour (valid-by-construction)

The teacher model supplies only **content**; the generator owns the **wire format**
and the harness mechanics the teacher must never fake. A confused teacher can never
inject a malformed call, an orphan tool result, or a fake budget line into training.

The teacher returns strict JSON (one object per trajectory):

```jsonc
{"trajectories": [ {
  "task": "…", "category": "…", "subcategory": "…",
  "outcome": "solved_clean|solved_late|recovered|max_iterations",
  "steps": [ {
    "think": "concise first-person reasoning about why this tool/args now",
    "tool":  "Read",                       // must be a catalog name
    "args":  {"file_path": "/repo/…"},     // must match that tool's JSON-Schema
    "result":"realistic tool output (an error string for failed steps)",
    "error": true                          // optional; true only for failed steps
  } ],
  "final": "concrete closing summary (or honest partial summary for max_iterations)"
} ] }
```

`generate_fullfix_data.py` then, for each trajectory:

1. builds the Lamark system turn + the task `user` turn;
2. validates every step's tool name against the catalog and every step's `args`
   against that tool's required params — **drops the whole trajectory on any miss**;
3. assigns `call_N` ids and emits `assistant(tool_calls=[…])` + matching `role:"tool"`;
4. injects the **budget** turns and (for `max_iterations`) the **forced summary**;
5. runs `agentic_format.validate_row()` — rejecting half-open think tags, orphan tool
   results, narrated `Name(...)` calls, and empty assistant turns.

### Budget mechanics

The iteration budget `M` (default 15) is a fixed cap so `k/M` and "j left" are exact:

- A `[BUDGET: Iteration k/M. j left …]` `user` turn is injected before each of the
  **last `WARN_AT` (=4) iterations**. So `solved_clean`/`recovered` runs that finish
  early carry no warnings; `solved_late`/`max_iterations` runs do.
- `max_iterations` runs consume every iteration (`M = #steps`) and end with a forced
  `[BUDGET: Iteration limit reached (M/M). … Summarize what you accomplished, what
  remains …]` turn, then the assistant's honest partial summary.

These strings are owned by the generator (faithful to the dataset's observed
`[BUDGET: …]` injections and the upstream "be CONCRETE — file paths, command outputs,
error messages, line numbers" summary guidance), never by the teacher.

---

## Running it

`generate_fullfix_data.py` shares the LiteLLM client and tool catalog with
`generate_agentic_data.py` (no duplication). It needs the teacher proxy reachable
(`LITELLM_BASE_URL`, default `http://10.212.212.1:4000/v1`); never greedy (invariant 9).

```sh
cd learning/scripts

# preview the teacher prompt + plan, no API calls
python generate_fullfix_data.py --dry-run

# generate (rounds rotate the task focus; per-call trajectories per teacher call)
python generate_fullfix_data.py --rounds 4 --per-call 2 --budget 15 \
    --out ~/.lamark/data/fullfix_trajectories.jsonl
```

It is wired into the one-entrypoint orchestrator and the assembler:

```sh
# full corpus, including the full-fix step (step 2b)
python generate_all.py                 # add --skip-fullfix to leave it out
python generate_all.py --dry-run       # see the plan

# the assembler bucket (build_dataset.py)
python build_dataset.py --facts ../data/lamark_facts.jsonl \
    --fullfix-trajectories ~/.lamark/data/fullfix_trajectories.jsonl \
    --out-dir ~/.lamark/data
```

`generate_all.py` knobs: `--fullfix-rounds` (4), `--fullfix-per-call` (2),
`--fullfix-budget` (15). Output `fullfix_trajectories.jsonl` lands in the run's
`--out-dir` and is passed to `build_dataset.py --fullfix-trajectories`, which folds
it into the trajectory bucket alongside the template, teacher, and HF trajectories.

> Full-fix trajectories carry the full core `tools[]` array. At assembly time most
> are rendered **schema-free** (the model recognizes core tools from training instead
> of an embedded schema block) — see [tool-internalization.md](./tool-internalization.md).

---

## Invariants honored

- **Canonical, model-neutral.** Reasoning lives in the `thinking` field, never baked
  as `<think>` in content; tool calls are native `tool_calls[]`. The trainer renders
  per-family tokens via `model_template`.
- **Valid-by-construction.** Tool names + required args validated against
  `tools.yaml`; `validate_row()` gates every emitted row.
- **English-only, Lamark-named** (invariants 2, 7).
- **Never greedy** (invariant 9): `temperature 0.7, top_p 0.9, top_k 20`.
