# Batch D audit — scenarios 15–16

Consolidated audit for nightly training pipeline and forgetting probe / auto-rollback. 5 new gaps (G-065..G-069); all blockers.

---

## Scenario 15 — Nightly training

**Verdict:** 🟡 Yellow — pipeline structure is well-specified; four integration bindings between loops are missing.

### Coverage matrix

| Concern | Status | Evidence |
|---|---|---|
| Nightly pipeline timeline (plan/10:17–77) | ✅ | Complete specification; detailed sections in plan/10 |
| Trace exporter (`lamark trace export`) | ✅ | plan/02:36 + plan/10:172–182 KB connector |
| Secrets + PII redaction stages (10:29–35) | ✅ | Stage-1 and stage-2 documented with tooling |
| Nemotron-Agentic-v1 transformer (10:37) | ✅ | Specified in diagram |
| Two-judge curation (10:39–42) | ✅ | Claude+GPT / GPT+Gemini, both ≥ 4/5 |
| Quality / dedup / 13-gram (10:44–47) | ✅ | Concrete thresholds; CI decontamination check |
| Blend 65/20/10/5 (10:49–54) | ⚠️ | Percentages specified; `blend_config.toml` file path and schema missing — **G-066** |
| Trainer Unsloth / Megatron-Bridge (10:56–62) | ✅ | Both paths documented with hyperparameters |
| Eval gate Bonferroni + `tool_call_compliance` (10:64–68) | ✅ | `tool_call_compliance ≥ 0.995` at plan/10:68 |
| Forgetting probe thresholds (10:70–72 + scenario 16) | ✅ | 1/2/3/5pp tiers specified |
| Promotion + vLLM hot-swap (10:74–76) | ✅ | plan/10:315–325 — explicit vLLM API calls + KB event |
| Idempotency / reproducibility | ⚠️ | Depends on G-003 (open blocker) |
| Rollout serialization with Loop C (G-035) | ⚠️ | G-035 tracked; ADR-0037 pending |
| Curator post-promotion re-run (plan/08:155) | ❌ | plan/08:155 documents cron + post-monthly-merge triggers; `AdapterPromoted` event trigger absent. **G-065** |
| Adapter lineage in KB (10:76) | ✅ | plan/10:323 — lineage fields in KB event |
| DPO pair input (G-008 / G-012 / G-021) | ⚠️ | Correctly references existing open blockers |

### New gaps (G-065..G-066)

- **G-065 [blocker]** — Curator event trigger on `AdapterPromoted` — `plan/08` + `plan/10` — ADR needed
  - plan/08:155 only lists cron + post-monthly-merge; `AdapterPromoted` never mentioned.
  - Breaks the agent-side loop: new adapter → Curator re-evaluation → skill refresh.
- **G-066 [blocker]** — Blend config file path, schema, and anchor-bump logic — `plan/10` — ADR needed
  - plan/10 specifies percentages but not `blend_config.toml` path, TOML schema, increment amounts, normalization, or reset-on-success.

### Self-improvement assertions check

| Assertion | Status |
|---|---|
| Loop closes end to end | ⚠️ At risk — depends on G-065 (Curator trigger), G-066 (blender config), G-068 (Reflexion on rollback) |
| Anchors prevent catastrophic forgetting | ✅ 10/5% frozen mix; 5pp rollback threshold |
| Reproducibility | ⚠️ Depends on G-003 (open blocker) |
| Decontamination enforced | ✅ 13-gram CI check |
| Lineage queryable | ✅ plan/10:323 + `lamark adapter lineage <id>` |

---

## Scenario 16 — Forgetting rollback

**Verdict:** 🟡 Yellow — probe mechanics and rollback command are well-specified; KB event schemas, Reflexion binding, and active-session deferral are missing.

### Coverage matrix

| Concern | Status | Evidence |
|---|---|---|
| Forgetting probe thresholds 1/2/3/5pp (10:70–72) | ✅ | plan/10:304–311 — all tiers documented |
| 100-prompt frozen probe set per base (10:70–72) | ✅ | plan/10:305 — "100 prompts each per base model" |
| Auto-rollback on 5pp regression (10:70–72 + 10:74–76) | ✅ | plan/10:311 + 315–320 — vLLM commands explicit |
| vLLM unload + reload previous adapter (10:74–76) | ✅ | Explicit `curl` commands at plan/10:317–320 |
| KB `AdapterRejected` event schema (10:76) | ❌ | plan/10:323 mentions schema but provides no JSON/Kotlin field spec. **G-067** |
| Anchor-percentage bump in blender config | ❌ | See G-066 — mechanism missing |
| Reflexion lesson on rollback (plan/07b Loop B) | ❌ | plan/07b §Loop B assumes post-session hooks; no trigger spec for rollback events. **G-068** |
| Rolling 7-day regression history in KB (10:70–72) | ✅ | Implicit in "2pp/7d" threshold at plan/10:309 |
| Probe set immutability + decontamination (10:47) | ✅ | 13-gram at plan/10:232; probe immutable by design |
| Operator alert on rollback (plan/11 ops) | ✅ | plan/10:400 — retry + alert logic; plan/11 ops channel |
| Rollback during active sessions | ❌ | Not in plan/10 or plan/05c. **G-069** |

### New gaps (G-067..G-069)

- **G-067 [blocker]** — KB event schemas for `AdapterSuspended` and `ForgettingWarn` — `plan/10` — ADR needed
  - plan/10:323 only documents `AdapterPromoted` and `AdapterRejected`; suspension + warning schemas absent.
  - Dashboards and alert integrations cannot be built without schema.
- **G-068 [blocker]** — Reflexion lesson authoring triggered by rollback events — `plan/07b` + `plan/10` — ADR needed
  - plan/07b Loop B assumes post-session failure; no spec for rollback → lesson trigger.
  - Rollback → blender tuning feedback loop cannot close without this.
- **G-069 [blocker]** — Rollback deferral during active sessions — `plan/10` + `plan/05c` — ADR needed
  - Scenario specifies: active sessions drain → rollback fires OR 30-min deadline triggers.
  - Requires: session-status query endpoint, vLLM soft-unload semantics, deadline timer. None in plan/10 or plan/05c.

### Self-improvement assertions check

| Assertion | Status |
|---|---|
| Rollback is the floor | ✅ 5pp threshold enforced |
| Anchor bumps are self-correcting | ⚠️ At risk — depends on G-066 (blender config file) |
| Rollback history improves future training | ⚠️ At risk — depends on G-068 (Reflexion on rollback) |
| Probe set is frozen | ✅ 13-gram decontamination applies |

---

## Key observations

1. **Gaps are integration points, not core algorithm gaps.** Individual pieces (probe, rollback, blender, Reflexion, Curator) are specified; what's missing is the event bindings that connect them. G-065 (Curator ← AdapterPromoted), G-068 (Reflexion ← rollback event), and G-066 (blender config file ← anchor bump trigger) are all the same pattern.

2. **G-065 + G-068 block the agent-side self-improvement loop.** Without Curator triggering on adapter promotion and Reflexion triggering on rollback, the "loop closes end to end" assertion in scenario 15 §3 cannot hold.

3. **G-066 blocks the blender feedback controller.** Both scenarios assert anchor-bump is self-correcting, but with no `blend_config.toml` spec, the bump cannot be implemented.

4. **G-069 is a safety gap.** Uncontrolled rollback mid-session can corrupt in-flight conversations. Needs session drain + deadline timer.

---

## Summary

| Gap | Severity | Spans |
|---|---|---|
| G-065 Curator trigger on `AdapterPromoted` | blocker | 15 |
| G-066 Blend config path + anchor-bump schema | blocker | 15 / 16 |
| G-067 KB schemas for `AdapterSuspended` + `ForgettingWarn` | blocker | 16 |
| G-068 Reflexion lesson trigger on rollback | blocker | 15 / 16 |
| G-069 Rollback deferral during active sessions | blocker | 16 |

**5 blockers, 0 deferred.**
