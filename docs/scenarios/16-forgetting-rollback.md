# 16 — Forgetting probe → auto-rollback

> **Phase:** P8.
> **One-liner:** After every LoRA promotion (scenario 15), a frozen
> 100-prompt forgetting probe runs per base model. If regression
> exceeds the threshold (5pp / 7d rolling) the system auto-rolls back
> to the previous adapter, writes `AdapterRejected` to KB, and pages
> the operator — all without human intervention.

---

## North-star contribution

Scenario 16 is the **safety net** for the model-side self-improvement
loop. Without it, a LoRA that improves domain performance can silently
destroy general-capability baselines. The forgetting probe closes the
loop: every nightly training cycle is bounded — it cannot make the
model worse on retained tasks beyond a measured threshold.

- **Domain quality.** Operator can trust that overnight adapters are
  at worst marginally worse on general tasks; rollback is automatic.
- **Agent-side self-improvement.** Rollback event triggers a
  post-mortem Reflexion lesson (Loop B, scenario 07): *why did this
  training batch cause forgetting?*
- **Model-side self-improvement.** Rollback history feeds the blender:
  the next nightly cycle bumps anchor percentages (general + safety
  frozen mix) to compensate for drift.

### Signals produced / consumed

- **Consumes:**
  - Promoted adapter metadata from KB (`AdapterPromoted` event).
  - Frozen forgetting-probe set: 100 prompts per base model, stored in
    KB (`plan/10:70–72`).
  - Rolling 7-day regression history from KB.
- **Produces:**
  - Forgetting-probe scorecard (per-capability bucket scores).
  - Verdict: `pass | warn | bump_anchor | suspend | rollback`.
  - `AdapterRejected` or `AdapterSuspended` event in KB.
  - Rollback event triggers anchor-percentage bump in blender config.
  - Reflexion lesson written to KB (failure analysis).

---

## Idea

Nightly training (scenario 15) promotes a new adapter at ~12:00. Immediately
after `vLLM load_lora_adapter` succeeds, the forgetting probe fires:
`lamark-trainer/eval/forgetting.py --adapter <id> --base <base_id>`.
It runs the 100-prompt frozen set against the live in-process provider.
Results are compared to the rolling 7-day baseline. Threshold logic fires
automatically. If rollback: `vLLM unload_lora_adapter`, previous adapter
reloaded, KB updated, operator paged.

## Actors

| Actor | Role |
|---|---|
| **Forgetting prober** | `lamark-trainer/eval/forgetting.py` — drives 100 frozen prompts through the in-process provider. |
| **Threshold engine** | Computes per-capability regression vs 7-day baseline; applies tier logic. |
| **Rollback executor** | `lamark-trainer/promote/rollback.py` — calls `vLLM unload_lora_adapter`; reloads previous. |
| **KB client** | Writes rollback / suspension events; reads adapter lineage. |
| **Blender config writer** | Bumps anchor percentages in `blend_config.toml` on sustained drift. |
| **Operator pager** | Fires `PagerDuty / Slack` alert on rollback. (ops — `plan/11`). |
| **Reflexion writer** | Writes post-mortem lesson to KB (scenario 07 Loop B). |

## Trigger

Automatic, immediately after `AdapterPromoted` event lands in KB (emitted
by scenario 15 step 10). Also triggerable manually:

```
lamark-trainer eval forgetting --adapter <adapter_id> --base <base_id>
```

## Pipeline

1. **Load probe set.** Fetch frozen 100-prompt set for the base model from
   KB. Set is immutable — never changes after base-model registration.
2. **Run probe.** Send all 100 prompts to the in-process provider (with
   new adapter loaded). Collect scores per capability bucket (reasoning,
   code, instruction-following, safety, factual recall).
3. **Compute regression.** For each bucket: `delta = probe_score - rolling_7d_baseline`.
4. **Apply threshold tiers:**
   - `|delta| < 1pp` → **pass** (log; no action)
   - `1pp ≤ |delta| < 2pp` → **warn** (log; no rollback)
   - Rolling 2pp regression for 7 consecutive days → **bump anchor**:
     increment `general_anchor_pct + 2` and `safety_anchor_pct + 1` in
     blender config for next nightly run.
   - `2pp ≤ |delta| < 3pp` single day → **suspend**: do not promote
     further adapters until resolved; KB records `AdapterSuspended`.
   - Rolling 3pp for 7 days → **suspend + alert**.
   - `|delta| ≥ 5pp` (any day) → **immediate rollback**.
5. **Rollback (if triggered).** `vLLM unload_lora_adapter(new_adapter_id)`.
   `vLLM load_lora_adapter(previous_adapter_id)`. Verify in-process
   provider responds with expected adapter metadata.
6. **KB write.** `POST /agents/{id}/events` with:
   - `kind: AdapterRejected` (rollback) or `AdapterSuspended` or `ForgettingWarn`.
   - Full probe scorecard (per-bucket deltas, verdict, threshold tier hit).
   - Lineage: `previous_adapter_id`, `rejected_adapter_id`, timestamp.
7. **Blender config update** (if `bump_anchor`). Write new percentages to
   `~/.lamark-trainer/blend_config.toml`. These take effect on the next
   nightly cycle.
8. **Reflexion lesson.** If rollback or suspend: write a KB memory entry:
   - `kind: Lesson`; `body`: "Adapter <id> caused Xpp forgetting on
     <bucket>. Training batch: <data window>. Likely cause: <...>."
   - Lesson is consumed by next Curator run and informs blender tuning.
9. **Operator alert.** If rollback: fire alert via `plan/11` ops channel
   (Slack + PagerDuty). Include probe scorecard + rollback details.

## Layers / crates touched

| Step | Crate / module | Plan |
|---|---|---|
| 1 (load probe set) | KB API + `lamark-kb-client` | 07a + 10 |
| 2–3 (run + compute) | `lamark-trainer/eval/forgetting.py` | 10:70–72 |
| 4 (threshold engine) | `lamark-trainer/eval/forgetting.py` | 10:70–72 |
| 5 (rollback) | `lamark-trainer/promote/rollback.py` | 10:74–76 |
| 6 (KB write) | `lamark-kb-client` | 10:76 |
| 7 (blender config) | `lamark-trainer/blend/config.py` | 10:49–54 |
| 8 (Reflexion lesson) | `lamark-kb-client` + scenario 07 Loop B | 07a + 07b |
| 9 (alert) | `plan/11` ops | 11 |

## Failure modes

| Failure | Expected behavior |
|---|---|
| **Probe prompts fail (provider error)** | Mark probe run as `incomplete`; do not rollback; alert. |
| **KB unreachable during probe** | Probe runs locally; results queued for upload; rollback proceeds on local verdict. |
| **vLLM unload fails** | Retry 3×; if still failing, mark adapter as `stuck`; page operator; do NOT load new sessions. |
| **Previous adapter unavailable** | Hard failure: alert operator; keep new adapter loaded (degraded mode). |
| **Probe set is stale (base model updated)** | Detected by `base_model_hash` mismatch; abort probe; alert. Probe set must be regenerated. |
| **Rollback during active session** | Active sessions using new adapter complete; rollback deferred until `active_sessions == 0` OR deadline (30 min). |
| **Anchor bump exceeds 30% combined** | Cap at 30% (general + safety); alert that frozen anchors are crowding out new data. |

## Acceptance criteria

- [ ] Forgetting probe runs automatically within 5 minutes of `AdapterPromoted` event.
- [ ] All 5 capability buckets scored; delta computed against 7-day baseline.
- [ ] 5pp threshold triggers rollback without operator input.
- [ ] Rollback verifies previous adapter is live before writing `AdapterRejected`.
- [ ] KB `AdapterRejected` event includes full scorecard and lineage.
- [ ] Blender config is updated atomically (write to tmp → rename).
- [ ] Reflexion lesson is written to KB on rollback.
- [ ] Operator alert fires on rollback (CI test: alert hook called).
- [ ] Probe run is idempotent: re-running with the same adapter + probe set produces the same verdict (modulo provider non-determinism, captured by seed).

## Self-improvement assertions

1. **Rollback is the floor.** No nightly training cycle can produce an
   adapter that's more than 5pp worse on any capability bucket. The
   floor is enforced mechanically.
2. **Anchor bumps are self-correcting.** Sustained drift → higher frozen
   anchor percentage → next cycle regresses less → anchor percentage
   stabilizes. The blender config is a feedback controller.
3. **Rollback history improves future training.** Reflexion lessons from
   rollback events + KB lineage let the trainer team diagnose systematic
   causes (e.g., "batches dominated by code traces always cause reasoning
   forgetting").
4. **Probe set is frozen.** The 100-prompt forgetting probe is never
   contaminated by training data (13-gram decontamination applies, same
   as the eval suite). This guarantees the floor is measurable.

## Plan coverage matrix

| Concern | Covered in | Status |
|---|---|---|
| Forgetting probe thresholds (1/2/3/5pp) | plan/10:70–72 | _audit_ |
| 100-prompt frozen probe set per base | plan/10:70–72 | _audit_ |
| Auto-rollback on 5pp regression | plan/10:70–72 + 10:74–76 | _audit_ |
| vLLM unload + reload previous adapter | plan/10:74–76 | _audit_ |
| KB `AdapterRejected` event schema | plan/10:76 | _audit_ |
| Anchor-percentage bump in blender config | plan/10:49–54 | _audit_ (partial) |
| Reflexion lesson on rollback | plan/07b (Loop B) | _audit_ |
| Rolling 7-day regression history in KB | plan/10:70–72 | _audit_ |
| Probe set immutability + decontamination | plan/10:47 (13-gram) | _audit_ |
| Operator alert on rollback | plan/11 ops | _audit_ |
| Rollback during active sessions (deferred) | (gap — not in plan/10) | _audit_ |
