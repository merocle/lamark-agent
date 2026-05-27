# 07 — Prompt self-improvement (Reflexion + OPRO + A/B promote)

> **Phase:** P5+ (loops A & B at P5; loop C piggybacks on the training pipeline P7).
> **One-liner:** A failed session produces a Reflexion lesson (Loop B, agent-side, runtime). A week later, OPRO (Loop C, model-side-adjacent) proposes new variants of the `guidance.memory` prompt section; A/B test picks the winner; trace recorder captures which `section_id` was active per turn so the gain is *attributable*.

---

## North-star contribution

- **Domain quality.** Without Loop A/B/C, the same mistakes happen forever. The agent's *system prompt* — the seat of its identity and policy — would never improve.
- **Agent-side self-improvement.** This scenario *is* agent-side self-improvement at its most explicit. The three loops (Self-Refine / Reflexion / OPRO) are exactly the techniques `plan/07b` surveys; the scenario tests they actually wire together.
- **Model-side self-improvement.** Prompt evolution and model training **must not entangle**. If we A/B-test a prompt section while a new LoRA is rolling out, attribution collapses. Loops C and the nightly training (scenario 15) must serialize or use a quasi-random `(prompt_id, adapter_id)` factorial — this scenario surfaces that constraint.

### Signals produced / consumed

- **Produces:** Reflexion `Episode` memory facts; OPRO-proposed prompt variants in `~/.lamark/prompts/proposals/`; A/B-test event stream; promoted prompt-section versions tagged with `section_id` and version.
- **Consumes:** trace bundles with `section_id` tags per turn (requires a small extension to the recorder); per-section outcome stats from KB.

---

## Idea

Tuesday: a session fails — the agent forgot to call `memory.search` before proposing changes to an unfamiliar codebase. Post-session hook generates a Reflexion lesson: *"before proposing edits in a new repo, always run memory.search with the project topic."* Stored in KB episodic memory, tagged `topic=cold_start_new_repo`. Friday: an OPRO batch job notices that `section_id=guidance.memory` has a 4pp regression over 7 days. It generates 8 variants of that prompt section, scores them against a held-out set, and proposes the best to A/B. Monday: A/B test runs (50/50, traffic-split per session); after 200 sessions, the winner promotes; loser archived. Tuesday's identical failure is now caught at the prompt level instead of at runtime.

## Actors

| Actor | Role |
|---|---|
| **Failed session (Loop B trigger)** | Any session ending with `reinforce_signal != success` (G-004). |
| **Reflexion post-session hook** | Runs the critique inference; writes `MemoryKind::Episode` (`plan/07b §"Loop B"`). |
| **OPRO batch job** | Nightly/weekly job (`plan/07b §"Loop C"`); part of the Python training pipeline (`plan/10`), but **prompt-only** — no adapter training. |
| **Prompt composer** | `crates/lamark-prompt/` — emits `section_id` per active section per turn; supports versioned sections; honors A/B traffic split. |
| **A/B test runner** | `crates/lamark-prompt::ab` (likely new) — assigns a session to a variant deterministically by `(rollout_id, section_id) → variant`. |
| **Promotion gate** | Eval-set scorer + minimum-sample threshold + statistical significance gate (z-test or sequential probability ratio). |

## Trigger

Three triggers, three loops:

- **Loop A (Self-Refine):** user runs `/refine` after a response; or auto-triggered on a structural check failure.
- **Loop B (Reflexion):** post-session hook on `reinforce_signal != success`.
- **Loop C (OPRO + A/B):** scheduled job; reads per-section outcome stats from KB.

## Pipeline — Loop B (Reflexion, runtime)

1. `TurnEnded { status: SessionEnd }` → recorder closes bundle → outcome is `fail` or `mixed` (G-004).
2. Post-session hook `reflexion_critique` runs an inference call: input is the *reduced* conversation + the outcome; output is a structured lesson.
3. Schema validator on the lesson (length ≤ 1 KB, has `pattern + action`).
4. `memory.write(MemoryEntry { kind: Episode, content: lesson, topic, source: agent_observation })` (`plan/07a §"Memory trait"`).
5. KB indexes it; future `memory.search` surfaces it when topic matches.
6. Reinforce dynamics: lesson reappears in next session → if outcome `success` → `reinforce(positive)`; if no help → `reinforce(negative)` and decay (`plan/07b §"Loop B"`).
7. **Caps:** ≤ 500 lessons per project; each ≤ 1 KB; topic-tagged.

## Pipeline — Loop C (OPRO + A/B, batch + runtime)

### C0 — Per-section outcome stats

8. Recorder must emit which `section_id`s were active per turn. This is a small extension to plan/06 events (gap: G-027 below).
9. KB aggregates daily: `(section_id, success_rate, tool_compliance, n_turns)` per project (`plan/07b §"Loop C"`).

### C1 — Regression detection

10. Nightly: load 7-day rolling stats per section. Flag sections regressing ≥ 2 pp vs baseline.

### C2 — Variant generation (OPRO)

11. For each flagged section: prompt a frontier model with `{current_section_text, sample_of_failing_traces, success_criteria}`. Ask for K=8 variants.
12. Schema-validate each variant. Constitutional-style safety filter rejects anything that drops core constraints (e.g., permission-first language).

### C3 — Held-out scoring

13. Each variant is scored on a small held-out trace set (replay-based, deterministic per G-003).
14. Top-2 variants advance to A/B.

### C4 — A/B test in production

15. Promote the two candidate variants into `~/.lamark/prompts/proposals/section=<id>/v=<n>.md` with `version` and `parent_section_id`.
16. Prompt composer's A/B runner assigns sessions deterministically: `variant = hash(rollout_id || section_id) mod 2`.
17. The recorder logs `section_variant_id` per turn so attribution is exact.
18. Run for `min_sessions = 200` OR until statistical significance reached, whichever first.

### C5 — Promotion / rollback

19. If winner's success_rate uplift is significant (z-test α = 0.05) AND it doesn't regress on the safety eval set (scenario 10 / 16) → **promote**. Old version archived under `~/.lamark/prompts/archive/`.
20. Loser archived. KB records the lineage (`SectionVersionEvent`).
21. If both variants regress → no promotion; the section returns to baseline; the failing case is logged for human review.

## Pipeline — Loop A (Self-Refine, optional)

22. Default off (`plan/07b §"Loop A"`). User opts in per-task or via hook.
23. Critique is captured as `AgentReasoningDelta`; revision becomes the turn. No persistence beyond the trace.

## Layers / crates touched

| Step | Crate | Plan |
|---|---|---|
| Loop B (Reflexion hook) | `lamark-hooks`, `lamark-memory` | 07b §"Loop B", 07a |
| Loop B (memory cap + decay) | `lamark-memory` | 07a + 07b |
| Loop C C0 (`section_id` per-turn logging) | `lamark-prompt`, `lamark-trace` (extension) | 07 + 06 (gap G-027) |
| Loop C C1 (regression detection) | trainer Python `plan/10` | 07b §"Loop C" |
| Loop C C2 (OPRO variant generation) | trainer Python | 07b §"Loop C" |
| Loop C C3 (held-out scoring) | trainer Python | 07b §"Loop C", 10 |
| Loop C C4 (A/B runner) | `lamark-prompt::ab` (new) | 07 + 07b (gap G-028) |
| Loop C C5 (promotion gate) | trainer Python + safety eval | 07b + 10 + scenario 16 |
| Loop A (Self-Refine) | `lamark-core` | 07b §"Loop A" |

## Failure modes

| Failure | Expected behavior |
|---|---|
| **Reflexion produces nonsense** | Schema validator rejects; not written to memory. Counter incremented; if N misses in a row, hook auto-disables for the project. |
| **Variant violates safety eval** | C5 rejects; loser archived. |
| **A/B traffic mis-split** | Hash-based assignment ensures reproducible distribution; sanity check in stats step. |
| **OPRO can't propose meaningful variants** | K runs returned same text or trivial whitespace edits → flagged; no A/B. |
| **Prompt change interacts with active LoRA rollout** | C5 *blocks* promotion if a new adapter is in its first 48h on the same model. Loops C and 15 must serialize. (Gap: needs explicit ADR — G-029.) |
| **Per-section log missing** | C0 won't aggregate; alert operator. Means recorder upgrade hasn't shipped yet. |

## Acceptance criteria

- [ ] Failed session writes ≥ 1 `MemoryKind::Episode` lesson; cap (500 / 1 KB) enforced.
- [ ] Re-surfacing of a lesson in the next session leads to a measurable behavior change on a planted regression test.
- [ ] Trace events carry `section_id` and (when A/B active) `section_variant_id` per turn.
- [ ] OPRO job proposes ≥ 1 variant when a section regresses ≥ 2 pp over 7d.
- [ ] A/B runner produces statistically valid attribution after 200 sessions (no traffic-split bias).
- [ ] Promotion requires both: success-rate uplift (α=0.05) AND safety eval green.
- [ ] Loser variants archive cleanly; lineage in KB.
- [ ] Loop A is opt-in; default off.

## Self-improvement assertions

1. **Reflexion closes a runtime loop.** Same failure pattern, second occurrence resolves correctly because the lesson was surfaced.
2. **OPRO produces measurable section-level uplift.** The 7-day rolling success rate of the section post-promotion exceeds pre-promotion by ≥ the threshold.
3. **Attribution is exact.** Given a session's `(rollout_id, turn, section_variant_id)`, the analyst can reconstruct which prompt was active.
4. **Loops do not entangle with training.** Prompt promotion is blocked during the first 48h of a new adapter rollout (or whatever the ADR pins) — A/B attribution stays clean.
5. **Self-improvement is bounded.** Episode memory cap + section archive + Curator decision table together prevent runaway self-modification.

## Plan coverage matrix

| Concern | Covered in | Status |
|---|---|---|
| Loop A — Self-Refine (opt-in) | plan/07b §"Loop A" | _audit_ |
| Loop B — Reflexion post-session hook | plan/07b §"Loop B" + plan/07a `Episode` kind | _audit_ |
| Reflexion lesson cap (≤ 500 / 1 KB) | plan/07b §"Loop B" | _audit_ |
| Loop C — OPRO/ProTeGi outline | plan/07b §"Loop C" | _audit_ |
| **`section_id` per-turn logging extension** | (gap G-027) | _audit_ |
| **A/B traffic-split runner** | (gap G-028) | _audit_ |
| Variant safety eval (Constitutional filter) | plan/07b §"Loop C" + plan/10 eval gate | _audit_ |
| Promotion gate with statistical significance | plan/07b §"Loop C" | _audit_ |
| Held-out scoring uses deterministic replay (G-003) | plan/06 reducer + plan/10 | _audit_ |
| Section lineage in KB | (likely **gap**; plan/08:122 is for skills) | _audit_ |
| **Serialization with adapter rollout** (avoid entanglement) | (gap G-029) | _audit_ |
| Memory cap + decay for Reflexion | plan/07a (decay general) | _audit_ |
| Prompt composer supports versioned sections | plan/07 §"layered composition" | _audit_ |
| Reinforce-on-use detection for lessons | plan/07a (see G-019 / scenario 03 OQ#5) | _audit_ |

## Open questions

1. **`section_id` taxonomy.** Stable IDs for every section (identity / guidance / skills / memory / custom)? Hierarchical? Pin in plan/07.
2. **Frontier-model dependency for OPRO.** Falls back to local LoRA if frontier is unavailable? Probably not for v0.1 — OPRO needs *better* than current.
3. **A/B sample-size policy.** 200 default — same across projects, or per-project tunable?
4. **Loop C cadence.** Nightly variant *generation* + weekly *promotion*? Or both nightly?
5. **Constitutional rules location.** YAML? Markdown? Where are the "core constraints" defined so the safety filter can reference them?
6. **Reflexion lesson lifecycle.** When does an Episode expire? Decay-based, or hard TTL?
