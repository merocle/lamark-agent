# 15 — Nightly training: trace → LoRA → promote

> **Phase:** P7–P8.
> **One-liner:** Nightly cron runs the 9-hour pipeline from `plan/10`:
> collect yesterday's traces from `~/.lamark/traces/` + KB → redact →
> transform to Nemotron-Agentic-v1 → curate → quality + dedup →
> blend (65/20/10/5) → pack → train LoRA → eval gate → forgetting
> probe → promote or rollback. **The actual model-side self-improvement
> loop, end to end.**

---

## North-star contribution

This is **the** model-side self-improvement scenario. If scenarios 01–14
produce trainable signal, scenario 15 *consumes* it and yields a better
model overnight. Quality lever: deterministic reducer (G-003) +
content-addressed sample IDs + 13-gram decontamination ensure that
"better" is *measurable*.

- **Domain quality.** Same agent in the morning is materially better
  at yesterday's failure modes — without any code changes.
- **Agent-side self-improvement.** Adapter promotion triggers a Curator
  re-run (`plan/08:155`) — skills/policy/memory may re-evaluate under
  the new model. Loops compose.
- **Model-side self-improvement.** This *is* the loop.

### Signals produced / consumed

- **Consumes:**
  - All trace bundles since last run (`lamark trace export --since=yesterday`).
  - KB episodic memory (Reflexion lessons from scenario 07).
  - DPO pair sets (scenarios 02, 04 via G-008 / G-012 / G-021).
  - Optional curated seeds (OSS-Instruct + judge consensus).
  - Eval gold set + forgetting probe set (KB-stored, `plan/10:67–72`).
- **Produces:**
  - One LoRA adapter (Unsloth on Qwen3/Gemma4, or Megatron-Bridge on Nemotron).
  - Eval scorecard.
  - Adapter promotion event in KB (`plan/10:76`).
  - Forgetting-probe verdict (rolled into scenario 16).

---

## Idea

03:00 local time, cron fires `~/lamark-trainer/scripts/nightly.sh`. Pipeline runs unattended. By 12:00 the next day, the in-process provider has hot-swapped to the new adapter via `vLLM load_lora_adapter` — or the adapter was rolled back and yesterday's stays put.

## Actors

| Actor | Role |
|---|---|
| **Cron / systemd timer** | Triggers nightly. `plan/11 §"CI/CD"` covers ops. |
| **Trainer (Python)** | `~/lamark-trainer/` — Unsloth, Megatron-Bridge, TRL, transformers, datasets, peft. |
| **Trace exporter** | `lamark trace export --since=yesterday` (`plan/02:36`) writes raw input JSONL. |
| **Secrets redactor** | gitleaks + trufflehog + detect-secrets (stage 1, `plan/10:31`). |
| **PII redactor** | Presidio + spaCy + GLiNER (stage 2, `plan/10:34`). |
| **Transformer** | Source → Nemotron-Agentic-v1 JSONL (`plan/10:37`). |
| **Curator (training-time)** | OSS-Instruct seeds + two-judge consensus (`plan/10:39–42`). |
| **Quality / dedup / decontaminate** | PPL outlier, MinHash-LSH, 13-gram (`plan/10:45–47`). |
| **Blender** | 65/20/10/5 mix (`plan/10:49–54`). |
| **Packer + Trainer** | Parquet pack → LoRA fit (`plan/10:56–62`). |
| **Eval gate** | MMLU-Pro / HumanEval / MBPP / SWE-Bench-Lite / BFCL / IFEval / MT-Bench / gold (`plan/10:64–68`). |
| **Promoter** | vLLM hot-swap + KB POST adapter metadata (`plan/10:74–76`). |

## Trigger

```
03:00 * * *  /usr/local/bin/lamark-trainer/scripts/nightly.sh >> /var/log/lamark/training.log 2>&1
```

## Pipeline

The full 9-hour timeline lives in `plan/10:17–77`. Scenario 15 asserts the **observable contract** at each gate:

1. **Collect (T+0).** Trace export must complete deterministically for the same time window. KB episode pull is idempotent.
2. **Redact stages.** Any verified gitleaks finding **blocks the sample** (not the run). Counters reported. PII substitution is type-preserving, salted, and **consistent within document** (`plan/10:35`).
3. **Transform.** Each input bundle reduces to ≥ 1 Nemotron-Agentic-v1 line (G-003 determinism is a precondition).
4. **Curate (optional).** Two-judge consensus (Claude+GPT or GPT+Gemini, both ≥ 4/5) and execution-based filter for code. Skip if teacher budget is 0.
5. **Quality.** Drop top + bottom 5% by PPL. MinHash-LSH dedup (Jaccard ≥ 0.85). 13-gram decontaminate against eval suite — **hard contract: no eval sample n-gram appears in training data**.
6. **Blend.** Exact mix:
   - 65% tonight's new
   - 20% rolling 30-day MSSR-weighted replay
   - 10% **frozen** general anchor (Tulu 3 + OpenHermes-2.5 + ...)
   - 5% **frozen** safety anchor (Aegis 2.0 + HelpSteer3 + NemoGuard)
7. **Pack + train.** ~5h on Spark or M3 Pro. Single LoRA adapter file out.
8. **Eval gate.** All gates must pass, Bonferroni-corrected. `tool_call_compliance ≥ 0.995` strict (`plan/10:68`).
9. **Forgetting probe.** 100-prompt frozen set per base. Verdicts:
   - 1pp regression → **warn**
   - 2pp / 7d → **bump anchor** percentages
   - 3pp / 7d → **suspend** new adapter
   - 5pp / 7d → **roll back** (scenario 16)
10. **Promote.** `vLLM load_lora_adapter` hot-swap. `POST /agents/{id}/adapters` + `POST /agents/{id}/events { kind: AdapterPromoted, ... }` to KB.
11. **Side effects.** Adapter promotion triggers Curator re-run (`plan/08:155`); prompt-evolution Loop C respects the 48h quiet window (G-035).

## Layers / crates touched

| Step | Crate / module | Plan |
|---|---|---|
| 1 (collect) | `lamark-trainer/collect/` + `lamark trace export` | 02 + 10 |
| 2 (redact) | `lamark-trainer/redact/` | 10:29–35 |
| 3 (transform) | `lamark-trainer/transform/trace_to_messages.py` | 10:37 |
| 4 (curate) | `lamark-trainer/curate/` | 10:39–42 |
| 5 (quality) | `lamark-trainer/quality/` | 10:44–47 |
| 6 (blend) | `lamark-trainer/blend/` | 10:49–54 |
| 7 (train) | Unsloth / Megatron-Bridge | 10:56–62 |
| 8 (eval gate) | `lamark-trainer/eval/` | 10:64–68 |
| 9 (forgetting) | `lamark-trainer/eval/forgetting.py` | 10:70–72 + scenario 16 |
| 10 (promote) | `lamark-trainer/promote/` + KB API | 10:74–76 |

## Failure modes

| Failure | Expected behavior |
|---|---|
| **Insufficient new samples** | Skip cycle; log; don't fall back to repeating yesterday. |
| **Secret leak detected** | Block the sample (not the run). Count surfaces in the report. |
| **Decontamination shrinks the dataset below threshold** | Skip cycle; alert. |
| **Eval gate fails** | No promotion; previous adapter stays; KB records `AdapterRejected` event. |
| **Forgetting probe ≥ 5pp regression** | **Auto-rollback** to prior adapter; KB records rollback event. (Scenario 16.) |
| **vLLM hot-swap fails** | Promotion reverts; KB records the failure; operator paged. |
| **Curator re-run triggers a flood of skill rewrites under the new model** | Per-run 30-action budget caps (`plan/08:172`). |
| **Trace export contains active rollouts (mid-session)** | Exporter skips them; warning. |

## Acceptance criteria

- [ ] `nightly.sh` completes in ≤ 9 hours on the target box.
- [ ] Pipeline is **idempotent**: re-running yesterday's run produces the same adapter hash given the same data (modulo Python-side non-determinism, which is captured in seed config).
- [ ] No eval-sample 13-gram appears in training data (CI test).
- [ ] Promotion writes `AdapterPromoted` to KB with full lineage (data window, base model, training config hash, eval scorecard).
- [ ] Rollback writes `AdapterRejected` + reason.
- [ ] After promotion, the runtime sees the new adapter on next session start (or via vLLM hot-swap mid-run).
- [ ] Adapter rollout is serialized with prompt-evolution Loop C (G-035).
- [ ] Curator re-run is triggered by the promotion event.

## Self-improvement assertions

1. **Loop closes end to end.** Day N's failures become Day N+1's improvements *without human in the loop* (except for safety gates).
2. **Anchors prevent catastrophic forgetting.** 10% general + 5% safety frozen mix bounds the drift on legacy capabilities.
3. **Reproducibility.** Given the same inputs + seeds, the adapter hash is stable.
4. **Decontamination is enforced.** Independent CI checker confirms no eval-set leakage.
5. **Lineage is queryable.** `lamark adapter lineage <id>` returns the data window, base model, eval scorecard, and promotion/rollback history.

## Plan coverage matrix

| Concern | Covered in | Status |
|---|---|---|
| Nightly pipeline timeline | plan/10:17–77 | _audit_ |
| Trace exporter (`lamark trace export`) | plan/02:36 + plan/10 | _audit_ |
| Secrets + PII redaction stages | plan/10:29–35 | _audit_ |
| Nemotron-Agentic-v1 transformer | plan/10:37 | _audit_ |
| Two-judge curation | plan/10:39–42 | _audit_ |
| Quality / dedup / 13-gram decontaminate | plan/10:44–47 | _audit_ |
| Blend ratio 65/20/10/5 (frozen anchors) | plan/10:49–54 | _audit_ |
| Trainer (Unsloth / Megatron-Bridge) | plan/10:56–62 | _audit_ |
| Eval gate (Bonferroni, strict tool_call_compliance) | plan/10:64–68 | _audit_ |
| Forgetting probe thresholds | plan/10:70–72 + scenario 16 | _audit_ |
| Promotion + vLLM hot-swap | plan/10:74–76 | _audit_ |
| Idempotency / reproducibility of nightly | (likely partial; depends on G-003) | _audit_ |
| Rollout serialization with Loop C (G-035) | scenario 07 G-035 | _audit_ |
| Curator post-merge re-run trigger | plan/08:155 | _audit_ |
| Adapter lineage in KB | plan/10:76 | _audit_ |
| DPO pair input from scenarios 02 / 04 | G-008 / G-012 / G-021 | _audit_ |
