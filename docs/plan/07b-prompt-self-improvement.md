# 07b — Prompt self-improvement strategy

> The agent rewrites its own prompts over time. Survey of the public
> literature on prompt self-improvement, what we adopt, what we re-implement,
> and how it sits inside the Lamark loop.

This is a design document, not a layer plan. It informs the prompt composer
(plan/07), the skills system (plan/08), and the training pipeline (plan/10).

## What "prompt self-improvement" actually means

Three different things get bundled under this label, often confusingly:

1. **In-context, single-session refinement.** The agent reflects on its own output and revises within the conversation. (Self-Refine, Reflexion.)
2. **Across-session prompt evolution.** The agent's *system* prompt — or specific sections of it — change over time based on accumulated outcomes. (OPRO, ProTeGi, Promptbreeder, APE.)
3. **Skill/strategy authoring.** The agent writes new skill files / strategies and improves them over time; the system prompt indexes them but doesn't itself change much. (Hermes's skill system, Voyager-style skill libraries.)

Lamark adopts all three, **separately**, with different safety profiles and different sources of truth.

## Public literature — what we read

Survey of techniques. Each row notes: what it does, what we take, where it lands in our system.

| Technique | One-line description | Adopt? | Where it lands |
|---|---|---|---|
| **Self-Refine** (Madaan et al., 2023; arxiv 2303.17651) | LLM critiques its own draft and revises in-loop. | ✅ runtime | optional `/refine` slash command + per-skill "refine pass" hook |
| **Reflexion** (Shinn et al., NeurIPS 2023; arxiv 2303.11366) | After a failed trajectory, generate a verbal "lesson" and store in episodic memory. | ✅ runtime + KB | post-failure hook writes a Reflexion entry to `lamark-memory` (Hindsight provider or KB episodic) |
| **OPRO** ("LLMs as Optimizers", Yang et al., 2024; arxiv 2309.03409) | Use the LLM to iteratively propose better prompts given a scored trajectory. | ✅ pipeline | nightly job that proposes new variants of underperforming sections |
| **ProTeGi** (Pryzant et al., 2023; arxiv 2305.03495) | "Automatic Prompt Optimization with 'Gradient Descent' and Beam Search." Builds a synthetic gradient over prompts from error analysis. | ✅ pipeline | weekly job; sections with regression eligible |
| **APE** (Zhou et al., 2023; arxiv 2211.01910) | Automatic Prompt Engineer: search + score loop. | ⚠ research only | not a v0.1 ship; references for the OPRO/ProTeGi job |
| **AutoPrompt** (Shin et al., 2020; arxiv 2010.15980) | Gradient-based discrete prompt search. | ❌ | gradient access on closed APIs is infeasible |
| **Promptbreeder** (Fernando et al., DeepMind 2024; arxiv 2309.16797) | Self-referential prompt evolution; mutation prompts evolve too. | ⚠ research-only at v0.1 | evaluate at v0.2 |
| **EvoPrompt** (Guo et al., 2024; arxiv 2309.08532) | GA-style evolution over a population. | ⚠ research-only | evaluate at v0.2 |
| **PromptAgent** (Wang et al., ICLR 2024; arxiv 2310.16427) | MCTS over prompt edits. | ⚠ research-only | evaluate at v0.2 |
| **DSPy + MIPRO** (Khattab et al., Stanford; arxiv 2406.11695) | Declarative LLM "programs" with bootstrapped few-shot + metric-driven prompt search. | ✅ pipeline-side | bootstrapping prompts for *task* sections (not identity) |
| **Voyager skill library** (Wang et al., 2023; arxiv 2305.16291) | Open-ended agent writes new skills as code; library grows. | ✅ runtime + KB | already in our skill system (plan/08); KB stores library |
| **Constitutional AI / RLAIF** (Bai et al., Anthropic 2022; arxiv 2212.08073) | Self-critique guided by a written constitution + RL from AI feedback. | ✅ training | weekly DPO uses Constitutional-style critic for some preference pairs |
| **Tülu 3 RLVR** (Lambert et al., 2024; arxiv 2411.15124) | RL from verifiable rewards on instruction-following. | ✅ training | seeds the safety + IFEval anchors; methodology applies to tool-call compliance |
| **DeepSeek-R1 / GRPO** (DeepSeek, 2025; arxiv 2501.12948) | Group Relative Policy Optimization + binary verifiable rewards; no learned reward model. | ✅ training | primary RL phase after SFT bootstrapping (Loop D below) |
| **Agent-RLVR** (Pan et al., 2025; arxiv 2506.11425) | Agent guidance (strategic plans + error feedback) + GRPO for SE agents; solves sparse-reward failure of vanilla RLVR. | ✅ training | primary algorithm for guided-then-GRPO training once SFT bootstrapping is done |
| **ReST meets ReAct** (Aksitov et al., Google, 2023; arxiv 2312.10003) | ReST applied to a ReAct agent; 2 iterations enough; small fine-tuned model matches large prompted model. | ✅ training | direct validation that our SFT loop works for multi-step tool-using agents |
| **V-STaR** (Hosseini et al., 2024; arxiv 2402.06457) | Joint generator SFT + verifier DPO; verifier-guided selection +4–17% over plain self-improvement. | ✅ training | failed rollouts → verifier DPO pairs; verifier is equivalent to a learned ORM |
| **Self-Discover** (Zhou et al., 2024; arxiv 2402.03620) | Agent discovers reasoning structures, stores them as templates. | ⚠ research-only | strategies subsystem (below) is a watered-down version |

## Three loops, three timescales

We separate self-improvement by frequency, because they have different safety profiles and different debugging needs:

### Loop A — Per-turn refinement (in-session, no persistence)

**Mechanism:** Self-Refine / `/refine` slash command.

**How it works:**
1. Agent produces a draft answer.
2. A meta-prompt asks the same model to critique the draft against a checklist (correctness, completeness, format, tool-use compliance).
3. The model produces a revised answer.
4. Original + critique + revision land in the trace bundle (`AgentMessageDelta` for the draft, `AgentReasoningDelta` for the critique).

**When invoked:**
- User runs `/refine` after a response.
- Auto-triggered when an output fails a *structural* check (malformed JSON, missing required tool call, schema violation).

**Persistence:** None. The revision becomes the conversation turn; the critique is captured in the trace for training but does not change the system prompt.

**Safety:** Self-Refine has known pitfalls — models often "over-edit," especially on creative tasks. We default `agent.self_refine.enabled = false`; opt-in per task or via hook.

### Loop B — Cross-session: Reflexion (episodic memory of lessons)

**Mechanism:** Reflexion-style verbal feedback after failed sessions, stored in episodic memory.

**How it works:**
1. Session ends with outcome ≠ `success`.
2. Post-session hook runs `reflexion_critique` against the reduced conversation:
   > "This session failed because <X>. Going forward, when you encounter <pattern>, do <Y> instead of <Z>."
3. The lesson is written as a `MemoryKind::Episode` entry, topic-tagged.
4. Next time the agent encounters a similar context, the memory provider surfaces the lesson into the Tier-3 prompt block.

**Knowledge-base mediates:** Lessons go to KB's episodic memory store. Retrieval uses dense + sparse + topic-graph hybrid (KB's RAPTOR). Lessons that get re-surfaced *and* the next session succeeds → `reinforce(positive)`; lessons that don't help → `reinforce(negative)` and eventual decay.

**Safety:** Bounded — lessons cap at N=500 per project; each capped at 1KB; topic-tagged so they only surface in the right context.

#### Reflexion on rollback (Loop B extension)

**G-068.** Loop B also fires on `AdapterRejected` and `AdapterSuspended` KB events, not only on failed sessions. This is the only case where Loop B runs without a session — it is event-driven, not session-driven.

**Trigger flow:**

1. The training pipeline emits `AdapterRejected` or `AdapterSuspended` as `AgentEvent` entries via `POST /agents/{id}/events`.
2. The KB client publishes them on the internal event channel (`tokio::sync::broadcast`).
3. The Loop B subscriber (in `lamark-memory` or the turn-loop post-session hook, whichever hosts Loop B) picks up the event and calls `reflexion_critique(context)` where `context` includes:
   - `adapter_id`
   - `rejected_reason` (as returned by the KB event payload)
   - per-bucket probe scores (fetched from KB: `GET /agents/{id}/adapters/{adapter_id}/probe_results`)
   - training data window (date range of the training set used for this adapter)

**Lesson authoring.** The critique produces a `MemoryKind::Lesson` entry with the following body template:

```
Adapter {id} rejected: {per-bucket deltas}.
Training window: {start_date}–{end_date}.
Likely cause: {critique_body}.
Suggested fix: increase anchor % for {bucket}.
```

**Scope.** The lesson is written to KB with `project_filter = Global` (applies across all projects), because adapter regressions reflect model-level or training-pipeline-level issues, not project-specific behaviour.

**Curator consumption.** The Curator (plan/08) reads lessons tagged `source=reflexion_on_rollback` in its next weekly cycle and may propose a `blend_config` adjustment (e.g. increasing the anchor percentage for the regressed bucket) as a `StrategyProposal`.

**No duplicate writes.** If an `AdapterRejected` event for the same `adapter_id` has already produced a lesson within the last 24h (checked by KB dedup on `(kind=Lesson, source=reflexion_on_rollback, reference=adapter_id)`), the write is skipped.

### Loop C — Across-week: Section-level prompt evolution (OPRO + ProTeGi + A/B runner)

#### A/B traffic-split runner

**G-034.** The A/B runner lives in `crates/lamark-prompt::ab`. Responsibilities:

**Variant registry.** Maintains a `PromptVariantRegistry` keyed by `section_id → Vec<(variant_id, weight)>`. Weights are non-negative floats; the runner normalises them to a probability distribution at draw time.

**Session-stable assignment.** On each new session, draws a variant per section according to the registry weights using a seeded per-session RNG (`session_id` as seed) so the choice is stable within a session but varies across sessions.

**Manifest recording.** Records the variant assignment in `manifest.json` under `prompt_variants: { section_id: variant_id, ... }` so the trace bundle knows exactly which text was active.

**Outcome recording.** After each session, calls `ab.record_outcome(session_id, reinforce_signal)` which appends to the variant's rolling outcome window (last 50 observations per variant).

**Automatic promotion candidate.** Welch's t-test is run over the rolling windows after each outcome record. When a variant's win rate exceeds the control by at least `ab.promotion_threshold` (default 5 pp) with p < 0.05, the runner emits a `VariantPromotionCandidate` event on the internal event channel. The OPRO loop (Loop C proper) consumes this event and initiates the shadow-run verification gates.

**Quiet window enforcement (G-035).** Before starting any new A/B test, the runner checks `~/.lamark/rollout_lock.json`. If `now < locked_until`, the runner skips starting the experiment and logs `RolloutLocked { until }`.

```
crates/lamark-prompt/
└── src/
    └── ab/
        ├── mod.rs              # pub re-exports
        ├── registry.rs         # PromptVariantRegistry
        ├── runner.rs           # session draw + outcome recording + t-test
        └── events.rs           # VariantPromotionCandidate
```

#### Adapter-rollout quiet window (Loop C serialization)

**G-035.** After any adapter or section promotion, no new A/B test starts for 48 hours. This prevents compounding changes that would make it impossible to attribute a regression.

**Lock protocol:**

1. When an adapter is promoted (`AdapterPromoted` KB event) or a prompt section is promoted (`SectionPromoted` KB event):
   - Write `~/.lamark/rollout_lock.json` with:
     ```json
     {
       "locked_until": "<ISO8601 timestamp + 48h>",
       "reason": "adapter_promotion" | "section_promotion",
       "adapter_id": "<id>"   // or "section_id" as appropriate
     }
     ```
   - Write atomically (tmp + rename).

2. The A/B runner (G-034) checks this file before starting any new experiment. If `now < locked_until`, skip and emit a `RolloutLocked` trace event.

3. The forgetting probe (scenario 16 / plan/10) also checks the lock before authorising a new promotion.

4. The lock file is a plain JSON file. If deleted manually, the lock is lifted immediately — no database record to clean up.

**CLI sub-commands:**

```
lamark rollout-lock status          # print current lock state (locked_until, reason, id)
lamark rollout-lock clear --confirm # remove the lock file
```

Both commands are gated by the existing policy system; `rollout-lock clear` requires an explicit `--confirm` flag and emits a `RolloutLockCleared` audit event.

**Mechanism:** Nightly/weekly batch job (in the training pipeline) that proposes new variants of **specific prompt sections** based on aggregated outcomes.

**How it works:**

1. Each section in Tier-1 / Tier-2 has a stable `section_id`. The trace recorder logs which sections were active in each turn.

2. The training pipeline aggregates per-section outcome stats over the last N days:

   ```
   section_id=identity.soul         turns=10k  success_rate=68%   tool_compliance=0.94
   section_id=guidance.kanban       turns=2k   success_rate=72%   tool_compliance=0.97
   section_id=guidance.memory       turns=10k  success_rate=68%   tool_compliance=0.94
   section_id=skills.coding.bash    turns=8k   success_rate=70%   tool_compliance=0.95
   ```

3. Sections whose outcomes regress vs the rolling baseline (e.g., −2pp over 7d) are flagged.

4. The OPRO step: ask a frontier model (Claude/GPT/Gemini) to propose K=8 variants of the flagged section, given:
   - The current text.
   - A sample of trace excerpts where outcomes were bad.
   - The success criteria.

5. The ProTeGi step: "Score and prune" the variants against a held-out test set (50–200 prompts that exercise the section). Keep the top 2.

6. A/B test the top 2 against the current incumbent over the next 24–48h:
   - Per-session randomization (deterministic per `session_id`).
   - Tracked outcomes feed the per-section stats.

7. If a variant wins by ≥ X pp on the chosen metric with significance (Bonferroni-corrected), it replaces the incumbent.

**Where the truth lives:** Sections are stored in knowledge-base, versioned. The runtime fetches the current production version at session start. Variants under A/B are also fetched; the chosen one is logged per session.

**Safety guards:**

- Only sections marked `mutable: true` participate. Identity (`SOUL.md`), safety anchors, and tool schemas are **immutable**.
- A "shadow run" of the new variant happens against a frozen probe set (a subset of the forgetting probe from plan/10) before going live.
- One section change per week max per agent; coordinated changes require human approval.
- Roll-back is one config flag (`prompt.section_overrides.<id>.use_baseline = true`).

### Loop D — Across-month: Skill authoring (Voyager-style)

**Mechanism:** Skills are markdown files the agent writes (plan/08). Curator consolidates them weekly. Promotion to the bundled set happens monthly when a skill reaches success-rate + frequency thresholds.

**How it works:**

1. After a successful session with ≥ 5 tool calls and a novel pattern (detected by embedding similarity vs existing skills < 0.7), the agent is prompted to author a skill.
2. The skill goes to `~/.lamark/skills/agent-authored/<slug>.md` AND to KB.
3. Curator (plan/08) reviews weekly:
   - Skills used > 5 times and helpful → keep, possibly rewrite for clarity.
   - Skills used 0 times in 30 days → archive.
   - Skills with overlapping coverage → consolidate.
4. Monthly: skills with ≥ 20 successful uses + ≥ 0.85 success rate are eligible for promotion to the bundled set (i.e., shipped with Lamark, indexed in Tier-1 by default).

**Knowledge-base mediates the lineage:** every skill's KB record holds its full history (created by which session, refined by which curator runs, promotion history). The training pipeline can use these as preference signals.

## Architecture: how Loops B/C/D plumb together

```
                ┌─────────────────────────────────────────────────┐
                │              Lamark runtime (Rust)               │
                │                                                  │
                │   ┌───────────┐    ┌───────────┐                 │
                │   │  Session  │───▶│ Trace     │── on TurnEnded ─┼────┐
                │   │  (turn    │    │ recorder  │                 │    │
                │   │   loop)   │◀──▶│           │                 │    │
                │   └─────┬─────┘    └───────────┘                 │    │
                │         │                                        │    │
                │         │ memory.build_prompt_block              │    │
                │         ▼                                        │    │
                │   ┌───────────┐    ┌───────────┐                 │    │
                │   │  Memory   │───▶│ KB client │── HTTP ─────────┼────┤
                │   │ provider  │◀───│           │                 │    │
                │   └───────────┘    └───────────┘                 │    │
                │                                                  │    │
                │   ┌───────────┐                                  │    │
                │   │  Skills   │───── KB sync ───────────────────┼────┤
                │   │  loader   │                                  │    │
                │   └───────────┘                                  │    │
                └──────────────────────────────────────────────────┘    │
                                                                        │
                ┌───────────────────────────────────────────────────────▼─┐
                │              knowledge-base (Kotlin/Spring)              │
                │                                                          │
                │   /memory  /knowledge  /search  /graph  /agents          │
                │   - facts, episodes, lessons, strategies                 │
                │   - skill records w/ lineage                             │
                │   - trace bundles                                        │
                │   - per-section outcome stats (Loop C)                   │
                │   - reinforce signals                                    │
                └──────────────────────────────────────────────────────────┘
                                          ▲
                                          │ HTTP
                                          │
                ┌─────────────────────────┴────────────────────────────────┐
                │       Training pipeline (Python; cron-driven)             │
                │                                                           │
                │   Nightly:                                                │
                │     - collect traces (KB)                                 │
                │     - filter / dedup / decontaminate                      │
                │     - SFT LoRA on the agent's trace dataset               │
                │                                                           │
                │   Weekly:                                                 │
                │     - aggregate per-section outcomes                      │
                │     - run OPRO + ProTeGi on flagged sections              │
                │     - shadow + A/B test variants                          │
                │     - DPO on preference pairs (incl. Loop B reflexion)    │
                │                                                           │
                │   Monthly:                                                │
                │     - promote bundled skills                              │
                │     - merge LoRA → base; requantize                       │
                │     - refresh anchors; rotate teachers                    │
                └───────────────────────────────────────────────────────────┘
```

## Data model in knowledge-base for self-improvement

KB tables / endpoints needed (informs the public-API RFC there):

| Object | Endpoint (KB) | Owned by |
|---|---|---|
| Prompt section | `POST /knowledge/prompt_sections` | KB primary store; runtime fetches |
| Section variant | `POST /knowledge/prompt_sections/{id}/variants` | OPRO/ProTeGi job |
| Section A/B trial | `POST /knowledge/prompt_section_trials` | Training pipeline; runtime reads "current trial assignment" |
| Section outcome metric | `POST /agents/{id}/sections/{id}/outcomes` | Trace reducer in the runtime |
| Reflexion lesson | `POST /memory/episodes` w/ kind=reflexion | Post-failure hook |
| Skill record | `POST /knowledge/skills` | Runtime + Curator |
| Skill lineage | `POST /knowledge/skills/{id}/lineage_events` | Curator + Monthly |
| Strategy | `POST /knowledge/strategies` | Coordinator post-session |
| Reinforce signal | `POST /agents/{id}/reinforce` | Trace reducer |

Lamark's `lamark-kb-client` exposes typed methods for each (already in plan/07a).

## What we do NOT do (anti-features)

- **Don't mutate the SOUL/identity section automatically.** Identity drift is an alignment risk; manual review only.
- **Don't run online prompt search.** Per-turn search would be slow and expensive; we only run offline (nightly/weekly).
- **Don't let one session's outcome change the prompt for the next session.** Outcomes aggregate over N sessions + a held-out test; single-session swings are noise.
- **Don't roll forward without a roll-back path.** Every variant has a deterministic "use_baseline" override.
- **Don't gradient-search through model weights from prompt outcomes** — that's what the training pipeline (plan/10) is for. Keep prompt improvement and weight improvement separated.

## Verification gates

Before any prompt section change takes effect in production:

1. **Schema validation.** The new section must parse, fingerprint, and not change the `depends_on` graph.
2. **Forgetting probe shadow run.** The variant runs against the 100-prompt forgetting probe (plan/10 §"Forgetting probe"). Drop > 2pp → reject.
3. **Composability test.** With every other Tier-1 section, the resulting prompt fits in `prompt.max_tier1_tokens`.
4. **Determinism guard.** Section change is deterministic; no timestamps, no UUIDs in the body.

Failures here roll back the variant automatically.

## CLI

```
lamark prompt sections list                                  # show all sections w/ status
lamark prompt sections show <id>                             # current text + history
lamark prompt sections diff <id> <variant_id>                # compare
lamark prompt sections trials                                # ongoing A/B trials
lamark prompt sections override <id> --use baseline|<v>      # manual pin
lamark prompt sections lock <id>                             # disable self-improvement on this section

lamark reflexion list [--topic ...]                          # show lessons
lamark reflexion delete <id>                                 # forget a lesson

lamark skills lineage <skill_id>                             # full history of a skill
```

## Slash commands

```
/refine                          # Loop A: refine the last response
/lesson "<text>"                 # Loop B: manually add a Reflexion lesson
/section show <id>               # show current prompt section
/skill new <slug>                # Loop D: prompt the agent to author a skill from the recent conversation
```

## Tests

- **`tests/self_improve/reflexion_post_failure.rs`** — fail a session → assert a `MemoryKind::Episode` entry was POSTed with `kind=reflexion`.
- **`tests/self_improve/section_outcome_logging.rs`** — synthesize 100 turns with sections A,B,C → assert the per-section outcomes land in KB.
- **`tests/self_improve/trial_assignment.rs`** — given two variants in trial, assignment is deterministic per session_id.
- **`tests/self_improve/forgetting_probe_block.rs`** — a variant that drops the probe by 3pp is rejected.
- **`tests/self_improve/identity_immutable.rs`** — attempting to mutate `identity.soul` rejects at config-load time.

## Cutover gates

- ✅ **Loop B working**: a planted failure produces a Reflexion lesson; next similar session surfaces it; outcome improves.
- ✅ **Loop C plumbing working**: section outcomes flow from runtime → KB → training pipeline → variants → A/B trial. (Won't yield improvements in v0.1; gates that the *plumbing* is correct.)
- ✅ **Loop D working**: an agent-authored skill that gets ≥ 20 successful uses is promoted to bundled.
- ✅ **Safety**: every guard in §"Verification gates" rejects bad inputs.

## Loop D — RL phase: guided-then-GRPO (post-SFT bootstrapping)

> This loop is the **successor to SFT-only training**, activated once the nightly SFT cycle has produced a base-competent adapter (typically after 2–4 weeks of data collection).

**Mechanism:** Agent-RLVR (arXiv 2506.11425) + DeepSeek-R1 GRPO (arXiv 2501.12948).

**How it works:**

1. **Guidance injection** (Agent-RLVR §3.1): before each rollout, the model receives:
   - A high-level strategic plan retrieved from the KB (`/knowledge/strategies?task_type=<class>`).
   - Dynamic error feedback from the last failed attempt on this task class (retrieved from KB episodic memory via `/memory/search?topic=<task_class>&kind=Episode`).
   This bridges the sparse-reward problem: the model has enough context to produce a non-degenerate trajectory even before RL has converged.

2. **Rollout sampling**: for each task in the RL batch, sample K=8 rollouts from the current `ModelProvider` with temperature > 0.

3. **Reward computation** (verifiable binary):
   - **Code tasks**: run the generated code in `lamark-sandbox`; reward = test pass rate (0.0–1.0).
   - **Tool-use tasks**: reward = `ToolCallEnded { ok: true }` rate across the trajectory.
   - **SWE-bench-style tasks**: reward = SWE-bench pass@1 on the task's test suite.
   These rewards are emitted as `VerifiedReward { rollout_id, score }` events and stored in the trace bundle.

4. **GRPO update**: score each of the K rollouts relative to the group mean (no separate critic model). Store the (winning, losing) trajectory pairs in `dpo_pairs.jsonl` for the nightly DPO trainer as a side effect.

5. **Iterate**: 1 RL epoch per nightly cycle after SFT; V-STaR verifier (arXiv 2402.06457) trained jointly on the DPO pairs; verifier-guided selection replaces majority voting for the next rollout batch.

**When activated:** `training.rl_phase.enabled = true` in `~/.lamark-trainer/config.toml`. Disabled by default until SFT produces a adapter with `tool_call_compliance ≥ 0.90` on the eval gate.

**Safety guard:** the RL phase must not reduce `tool_call_compliance` below 0.995 or trigger any forgetting-probe threshold. If it does, the RL phase is suspended and SFT resumes.

---

## Pointer: research artifacts and code references

For implementers, the closest open code references:

- **Reflexion**: `noahshinn024/reflexion` (Python). Direct port of the critique prompt is fine.
- **OPRO**: `google-deepmind/opro` (Python). We re-implement the meta-prompt structure in Rust calling the same teacher API.
- **ProTeGi**: `microsoft/LMOps` (specifically `prompt_optimization/`). The "gradient" idea is just an error-analysis prompt.
- **DSPy MIPRO**: `stanfordnlp/dspy`. Our trainer (plan/10) can use DSPy directly for bootstrapping task-section few-shots.
- **Voyager**: `MineDojo/Voyager`. Skill library mechanics — we reuse the "skill description + when_to_use" frontmatter idea.
- **Constitutional AI / RLAIF**: Anthropic's HH-RLHF release notes. The structure of preference pairs from AI critique drives our DPO sampling.

No verbatim code copying — re-implement from the papers / READMEs.
