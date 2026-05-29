# 14 — Batch runner — offline eval / mass trajectory generation

> **Phase:** P7.
> **One-liner:** Operator runs `lamark exec batch.lamark` against a list
> of N (=1000) prompts; agent processes them concurrently with a
> bounded worker pool; each session writes a complete trace bundle;
> the resulting dataset feeds eval gates, OPRO scoring, and trainer
> input — all without a human in the loop.

---

## North-star contribution

- **Domain quality.** A research team can run "before / after" eval on
  a model change in hours, not weeks. Operations team can run a nightly
  health-check batch.
- **Agent-side self-improvement.** Batch runs are *high-volume signal*:
  Curator gets convergence data on skills (G-023) across thousands of
  trajectories at once; memory-extraction (G-014) writes facts at scale;
  Reflexion (Loop B, scenario 07) has more failures to learn from.
- **Model-side self-improvement.** This *is* how training data is
  generated at scale. Without a batch runner, the trainer is starved.

### Signals produced / consumed

- **Produces:** N trace bundles in parallel; aggregate stats; safety-
  eval scores; held-out replay results; (optionally) DPO pair sets when
  a paired set of prompts is run.
- **Consumes:** prompt list (JSONL); eval gold set; concurrency config;
  budget per session; provider rate limits.

---

## Idea

`batch.lamark` lists 1000 evaluation prompts with expected behaviors. Operator runs `lamark exec batch.lamark --concurrency 32 --output ~/eval/runs/20260525/`. The batch runner spawns a pool of agent sessions, dispatches prompts, collects bundles. The eval gate (`plan/10`) scores each bundle. Aggregate stats land in KB; failing prompts produce Reflexion lessons; the dataset is now usable for SFT/DPO + held-out scoring.

## Actors

| Actor | Role |
|---|---|
| **Batch runner** | `crates/lamark-batch` (likely new — `plan/02:44` mentions `lamark exec SCRIPT.lamark` and `specs/01-scope-and-inheritance.md` references "Batch runner"). |
| **Worker pool** | Bounded set of `Session`s, each isolated (own rollout). |
| **Provider router** | Manages rate limits across the pool (`plan/04`). |
| **Eval scorer** | Plan/10 — per-bundle scoring against a gold set; pluggable judges. |
| **KB client** | Bulk upload of bundles + aggregate stats. |
| **Cancellation** | One signal kills the whole pool gracefully. |

## Trigger

```
$ lamark exec batch.lamark --concurrency 32 --output ~/eval/runs/2026-05-25/ --gold-set ~/.lamark/eval/gold.jsonl
```

`batch.lamark` is JSONL of `{prompt, expected, metadata}`. Or a TOML manifest pointing to a prompt source.

## Pipeline

1. **Parse manifest.** Validate prompts, expectations, per-prompt metadata.
2. **Plan run.** Compute `concurrency = min(--concurrency, provider_rate_cap, system_cap)`.
3. **Worker pool.** Spawn N sessions; each handles one prompt at a time; reuses provider connection where possible (cache prefix advantage on identical system prompts).
4. **Per-prompt session.** Each session writes a normal trace bundle under `--output/<id>/`.
5. **Provider rate limiting.** Centralized via `crates/lamark-providers::rate_limiter` (likely a gap — see G-051). 429 backoff respects `Retry-After`.
6. **Cancellation.** SIGINT → graceful: stop dispatching new prompts; cancel in-flight sessions; close bundles `status=aborted`. SIGTERM → forceful.
7. **Live progress.** TUI / stderr shows: `(n/N) succeeded / failed / aborted / in-flight`. Live tail of latest event per worker.
8. **Eval scoring.** As each bundle completes, the scorer runs against the gold set; per-prompt verdict.
9. **Aggregation.** Final report: pass rate, latency distribution, token cost, top failure modes, list of bundles for human review.
10. **KB upload.** Bulk-mode `POST /agents/{id}/traces/batch` (if KB supports) OR sequential. Idempotent via per-bundle `kb_upload_state.json`.
11. **Reflexion + memory extraction.** For each failure, post-session hook fires (scenario 07 Loop B). Memory extraction (G-014) runs per bundle.
12. **DPO pair authoring (optional).** If the manifest provides `paired_prompts`, the runner builds chosen/rejected pairs for DPO (G-008 / G-012 schema).

## Layers / crates touched

| Step | Crate | Plan |
|---|---|---|
| 1 (manifest) | `lamark-batch::manifest` | (new) |
| 2–3 (pool) | `lamark-batch::pool` | (new) |
| 4 (per-session) | `lamark-core` (unchanged) | 05 |
| 5 (rate limiting) | `lamark-providers::rate_limiter` | 04 (gap G-051) |
| 6 (cancellation) | `lamark-batch::cancel` + `tokio_util::sync::CancellationToken` | (new) |
| 7 (progress UI) | `lamark-batch::progress` | (new) |
| 8 (scoring) | `lamark-eval` (Python; calls Rust replayer) | 10 |
| 9 (report) | `lamark-batch::report` | (new) |
| 10 (bulk KB) | `lamark-kb-client::batch` | 07a (likely **gap G-052**) |
| 11 (Reflexion + memory) | scenario 07 + G-014 | 07a + 07b |
| 12 (DPO pair authoring) | G-008 + G-012 + new manifest field | 06 + 10 |

## Failure modes

| Failure | Expected behavior |
|---|---|
| **Provider 429 storms** | Rate limiter slows; per-worker exponential backoff; worker pool may shrink temporarily. |
| **Single session OOM** | That session aborts; pool continues. |
| **KB unavailable** | Bundles queue in outbox (G-017); upload retries; batch report flags pending. |
| **Disk full** | Pause new sessions; in-flight finish to disk if possible; notify operator. |
| **Cancellation mid-batch** | Partial report emitted; cancelled sessions marked; resume support via `--resume <output-dir>` (likely **gap G-053**). |
| **Manifest references missing gold-set entries** | Pre-flight check rejects; clear error. |
| **Two prompts produce same `rollout_id`** | Impossible by construction (UUID + counter), but tested. |
| **Worker deadlocks** | Per-session budget (default 600s) caps; worker reaps after `2 * budget`. |

## Acceptance criteria

- [ ] `lamark exec --help` documents the batch surface.
- [ ] 1000-prompt run completes; output dir has 1000 bundles; report present.
- [ ] Concurrency limit honored (no more than N in-flight).
- [ ] Provider 429s do not kill the run; rate limiter recovers.
- [ ] SIGINT yields a clean partial report.
- [ ] `--resume` continues an interrupted batch without re-running completed prompts.
- [ ] Eval scoring produces per-prompt pass/fail + aggregate metrics.
- [ ] KB upload is idempotent across re-runs.
- [ ] Failures produce Reflexion lessons.

## Self-improvement assertions

1. **Batch IS the training-data faucet.** A nightly batch run feeds the trainer enough samples for one LoRA cycle (scenario 15).
2. **Eval baseline reproducible.** Same manifest + same provider + same prompt cache → ≥ 95% identical verdicts run-to-run (determinism allowing).
3. **Failure clustering.** The report surfaces dominant failure modes; OPRO (scenario 07 Loop C) targets them next cycle.
4. **DPO pair production.** When the manifest specifies pairs, the runner emits a DPO-ready dataset directly (closes G-008 + G-012 at scale).

## Plan coverage matrix

| Concern | Covered in | Status |
|---|---|---|
| `lamark exec SCRIPT.lamark` CLI | plan/02:44 | _audit_ |
| Batch runner crate / module | SPEC §2.1 | _audit_ |
| Concurrency config | (likely **gap G-051** scope) | _audit_ |
| **Provider rate limiter** | (likely **gap G-051**) | _audit_ |
| **Bulk KB upload endpoint** | (likely **gap G-052**) | _audit_ |
| **Resume support (`--resume <dir>`)** | (likely **gap G-053**) | _audit_ |
| Eval scoring pipeline | plan/10 | _audit_ |
| Gold-set format | plan/10 | _audit_ |
| Cancellation semantics | plan/05c + tokio cancel | _audit_ |
| Live progress UI | (likely partial) | _audit_ |
| Output directory layout | (likely **gap**) | _audit_ |
| Per-prompt metadata propagation to manifest.json | plan/06 | _audit_ |
| DPO pair authoring at scale | G-008 + G-012 | _audit_ |
| Determinism contract for replay | G-003 | _audit_ |
