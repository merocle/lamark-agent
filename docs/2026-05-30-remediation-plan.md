# Remediation plan — bring Lamark to a healthy, publishable state

Date: 2026-05-30. Base: HEAD `50ae84a`, working tree clean.
Method: consensus-driven-development, adapted for a multi-fix remediation
(no YouTrack/Space/bazel). Diagnoses for the P0 items were verified
against source during the read-only assessment (see
`docs/v0.1-readiness-review.md` for the older, now-superseded review).

Constraint (reaffirmed by the maintainer): **all code, comments, and
prompts are English-only.** Multilingual behaviour is delivered solely by
the LLM at runtime (as in upstream Hermes) — never by hardcoded
non-English strings.

This plan is phased. Each phase is independently shippable. Phase P0 is
the priority: it repairs the core differentiating feature (nightly
self-improvement), which is currently broken in the automated path.

---

## Phase P0 — Repair the autonomous learning loop

The product's headline ("gets smarter every night") does not work in the
automated path today. Three defects, all verified in source.

### P0-1 — eval-gate probes a model vLLM never serves → every nightly adapter is auto-rejected

**Symptom.** Nightly runs on 2026-05-29 and 2026-05-30 were rejected by
the eval-gate. The 2026-05-28 adapter (`nightly-20260528T011715Z`) is the
last promoted one; it was promoted via a manual/seed path, not this code
path.

**Root cause (verified).**
- `scripts/cmd/serve.sh:196-198` mounts **only** `adapters/current` and
  advertises it as `--lora-modules lamark=/lamark/adapters/current`. vLLM
  therefore serves exactly two model names: `qwen-base` and `lamark`.
- `scripts/lamark-nightly-train.sh` trains `nightly-<TS>` (saved to
  `$ADAPTER_DIR/nightly-<TS>/`), restarts vLLM (at which point `current`
  still points at the *previous* adapter), then calls
  `eval_gate --adapter-name nightly-<TS>` (`lamark-nightly-train.sh:304-307`).
- `eval_gate._chat()` (`eval_gate.py:68-95`) sends `{"model":
  "nightly-<TS>"}`. vLLM does not serve that name → HTTP 404 → `_chat`
  returns `"<error: HTTP Error 404 ...>"`.
- All three probes then fail deterministically: identity finds no brand
  token in `<error...>`, safety finds no refusal signal, coherence
  explicitly rejects `"<error" in resp` (`eval_gate.py:170`).
- The `current` symlink is only repointed *after* a pass
  (`lamark-nightly-train.sh:318`), which can therefore never happen.

There is also a chicken-and-egg: a freshly trained adapter cannot be
gated because `serve.sh` only mounts what `current` already points at.

**Fix — promote-then-verify-then-rollback.** Reorder the orchestrator so
the candidate is served under the alias the gate probes, then rolled back
on failure. The training step already stops the production server to free
the GPU, so there are no live users during the train+gate window —
serving the unproven candidate during the gate is safe.

New ordering in `scripts/lamark-nightly-train.sh`:
1. `PREV_TARGET="$(readlink -f "$ADAPTER_DIR/current" 2>/dev/null || true)"`
   — remember the currently promoted adapter before touching anything.
2. Stop+rm `lamark-vllm` (unchanged).
3. Train `nightly-<TS>` (unchanged).
4. `ln -sfn "$ADAPTER_DIR/nightly-<TS>" "$ADAPTER_DIR/current"` — point at
   the candidate **before** serving.
5. `serve.sh start` → loads the candidate under the alias `lamark`.
6. `eval_gate --adapter-name lamark` (probe the **served alias**, not the
   timestamp name). One-line change at `lamark-nightly-train.sh:306`.
7. **Pass:** keep the symlink, run cleanup (see P0-3), notify success. The
   server is already serving the candidate — no extra restart needed
   (this also fixes the latent "promoted adapter not served until next
   restart" bug).
8. **Fail:** repoint `ln -sfn "$PREV_TARGET" "$ADAPTER_DIR/current"`
   (only if `PREV_TARGET` is non-empty and still exists), `serve.sh
   restart` to reload the previous adapter, `rm -rf` the candidate dir,
   notify rejection.

Edge case — first-ever run (no `current` yet): `PREV_TARGET` empty; on
fail just `rm` the candidate and restart with no adapter (serve.sh already
serves base-only when `current` is absent, `serve.sh:197`).

`eval_gate.py` needs **no change** for this fix (the `--adapter-name`
becomes the served alias). Probe-quality fixes are P2-1.

### P0-2 — `consumed_by` never written → cumulative set retrained at 5 epochs → over-fit, and the min-pairs gate is wrong

**Root cause (verified).** `consumed_by` is only ever initialised to `[]`
(`store.py:15,101`) and described in a docstring as "handled by the
dispatcher" (`curation.py:7`) — but it is **never written, appended, or
filtered anywhere** (confirmed: `git grep consumed_by` shows only the
schema init, the docstring, and a test asserting `== []`). Consequences:
- `build_nightly_plan` / `build_plan` (`curation.py:55-92,257`) re-emit
  the entire qualifying archive every run.
- `scripts/lamark-nightly-train.sh:262` overrides the dispatcher default
  of 1 epoch (`dispatcher_spark.py:117`) to **5 epochs at LR 2e-4** on
  that cumulative set — an over-fit recipe that compounds nightly.
- The "≥ min_pairs *new* content" claim (`README.md:215`,
  `lamark-nightly-train.sh:6`) is false — `N_PAIRS` is the cumulative
  curated count, so once you pass 50 you always "have enough", and the
  gate never reflects whether anything new arrived.

**Design note — why we do NOT exclude consumed pairs from training.** The
dispatcher trains a fresh LoRA *from the base model* each night
(`dispatcher_spark.py`), not incrementally from the previous adapter. If
we trained on new-only pairs, each night's adapter would forget everything
learned on prior nights. The training set must stay **cumulative** to
retain learning. `consumed_by` is therefore used for the run-decision
count and provenance, NOT to filter the training set.

**Fix.**
- **Consumed ledger (append-only, fits the archive's design).** Add a
  small sidecar `~/.lamark/archive/consumed.json` (a JSON object
  `{"<record-id>": {"adapter": "<name>", "promoted_at": "<iso>"}}`).
  Rationale: shards are append-only JSONL; rewriting them to set
  `consumed_by` in place violates that design and risks partial-write
  corruption. A sidecar is the minimal, crash-safe choice.
- **New Archive API** (`store.py`): `consumed_ids() -> set[str]` (read
  ledger, empty if absent) and `mark_consumed(ids: Iterable[str],
  adapter: str, now=None)` (merge-write the ledger durably via the
  existing fsync helper).
- **New-pair count for the gate.** Add a helper (curation or a thin CLI
  surface) that returns `len(qualifying_ids - consumed_ids())`. The
  nightly script's threshold check uses this NEW count, not the
  cumulative `N_PAIRS`. Training still runs on the full curated plan.
- **Mark on promote.** After a successful promote (P0-1 step 7), mark the
  plan's record IDs consumed by the new adapter name. Requires the plan
  to carry `meta.id`; verify `build_nightly_plan` preserves it and emit an
  ID manifest next to `train-plan-nightly.jsonl` if it does not.
- **Tame epochs.** Lower the nightly override from 5 to **2 epochs**
  (`lamark-nightly-train.sh:262`); keep LR 2e-4 for now. (Open tuning
  question for reviewers — see Risk R3.)
- **README correction (ties into P3-honesty).** Clarify that the
  *threshold* is on new pairs but *training* is on the full curated set.

### P0-3 — state-corruption windows in the orchestrator

**Root causes (verified, `lamark-nightly-train.sh`).**
- If training fails (`fail` at :263) or the box reboots mid-run, the
  production server was already stopped (:243-245) and is **never
  restarted** — the agent is left fully offline until manual
  intervention.
- Cleanup `ls -dt nightly-*/ | tail -n +3` (:327) keeps the two
  newest dirs *by mtime*. The dir that `current` resolves to is an
  ordinary `nightly-*` dir and can fall to position ≥3, getting
  `rm -rf`'d → `current` becomes a dangling symlink → `serve.sh:197`
  silently drops LoRA and serves bare `qwen-base` (silent personality
  wipe).

**Fix.**
- **Restart-on-failure.** Add a shell `trap ... EXIT`/`ERR` (or wrap the
  train+gate body) so that on any non-success exit, if the production
  server is down and a usable `current` exists, `serve.sh start` is
  attempted before exit. Never leave the agent offline silently; notify
  on this path.
- **Never delete the live target.** In cleanup, resolve
  `LIVE="$(readlink -f current)"` and exclude `$LIVE` from the deletion
  set explicitly, in addition to keeping the two newest. Guard the
  `rm -rf` so an empty/relative list can never expand dangerously.
- The "not served until restart" issue is already resolved by P0-1
  (the gate restart serves the candidate).

---

## Phase P1 — Make a non-owner install actually work

### P1-1 — verify and complete the install dependency closure

`install.sh:199-219` installs the Hermes **core** deps (they match
`vendor/hermes/pyproject.toml:34-54` pins closely) but deliberately skips
messaging/agent extras ("lazy-installed by hermes setup", :197-198) and
never runs `pip install -e .`.

**Uncertainty (must verify, do not assume).** It is NOT yet confirmed that
a non-owner install crashes — the core deps are present, and Hermes may
lazy-install messaging deps (e.g. `telethon`) during `lamark setup`. The
prior critic asserted a crash but did not run a clean install.

**Fix.** Empirically verify on a clean Linux container/VM as a non-owner:
run `install.sh`, then each `lamark setup` branch + `lamark chat` + the
gateway, capturing the first `ImportError` if any. Then add exactly the
missing deps (and/or `pip install -e .` for the `lamark` package) — no
more. Record the verification transcript in `docs/`.

### P1-2 — gate Spark-only quirks on hardware tier

`scripts/cmd/serve.sh:231` hardcodes `-e TORCH_CUDA_ARCH_LIST=12.1` and
`:238` runs `pip uninstall -y flash-attn flash_attn` on **every** serve,
unconditionally. The readiness review flagged these as "actively wrong on
Ada" (`docs/v0.1-readiness-review.md:147-157`). The same two lines appear
in the training container invocation (`lamark-nightly-train.sh`) and
`scripts/vllm_server.sh`.

**Fix.** Detect tier/`is_spark` (reuse `src/lamark/hardware.py`'s
detection — expose a tiny query the shell can call, or gate on an env set
by setup). On Spark: keep current behaviour. On non-Spark CUDA: set the
correct `TORCH_CUDA_ARCH_LIST` for the detected arch (or omit and let vLLM
infer) and **do not** purge flash-attn. Apply consistently in `serve.sh`
and the training container.

### P1-3 — resumable model download

`snapshot_download(...)` is called with no resume/retry wrapper on the
generic path; `HF_HUB_ENABLE_HF_TRANSFER=1` is set only on Spark.

**Fix.** Wrap downloads with retry + `resume_download=True`; make the
hf_transfer setting consistent and documented (hf_transfer disables resume
— pick one explicitly). 28-67 GB must survive a flaky home connection.

### P1-4 — soften the sudo/systemd cliff

System-scope timer install needs `sudo`; without it the nightly feature
silently degrades to manual.

**Fix.** When root/sudo is unavailable, auto-install the user-scope
(`systemctl --user`) timer + lingering instead of only printing a manual
hint. The gateway already uses user services (README:236-253); mirror
that for the trainer.

### P1-5 — resolve the three divergent vLLM stacks

`serve.sh:208` uses `vllm/vllm-openai:v0.21.0` (canonical, matches
README). `setup_spark.sh:37` pins `vllm==0.7.3`; `vllm_server.sh:70` uses
an unpushed local image `lamark/vllm:25.10` from `docker/Dockerfile.vllm`
with **unpinned** vllm/peft/trl.

**Fix.** Declare `vllm/vllm-openai:v0.21.0` canonical. Retire or clearly
mark `vllm_server.sh` + `Dockerfile.vllm` as Spark-native-only (or delete
if dead). Pin `peft`/`trl`/`transformers` everywhere they are installed
ad-hoc (training container, `setup_spark.sh`).

---

## Phase P2 — Tests & observability (so the next failure is diagnosable)

### P2-1 — eval-gate correctness + tests
- **Tests (new `tests/test_eval_gate.py`).** Monkeypatch `_chat`:
  (a) regression-lock P0-1 — an unknown model name (404 → `<error>`)
  fails all three probes; (b) the safety probe currently false-passes a
  *compliant* "Sure, I saved your secret" because `"secret"` is in the
  refusal-signal list (`eval_gate.py:143-146`) — assert it should fail,
  then fix by removing `"secret"`/tightening signals.
- **Drop the double-call.** `_probe_identity` runs the 4-probe loop
  (:114-119) then re-issues 2 probes (:124-127); the first loop's result
  is unused. Collapse to one pass.
- **Determinism.** `temperature=0.3` on a 2/2-required gate
  (`eval_gate.py:69,128`) can flip a borderline verdict. Use
  `temperature=0` for gate probes.

### P2-2 — nightly-script smoke test / shellcheck
Add a shell-level test asserting the gate is invoked against the served
alias (`lamark`) and after the candidate symlink swap, plus `shellcheck`
on `lamark-nightly-train.sh` and `serve.sh`.

### P2-3 — close the observability gap
Write the full `GateReport` (per-probe pass/detail) into
`train-history.jsonl` and into the rejection Telegram message (currently
"Eval gate failed" with no breakdown). Emit `new_pairs` vs
`cumulative_pairs` separately so `lamark train --status` can explain a
rejection streak. A user must be able to tell "data not good enough" from
"wiring bug" without reading code.

---

## Phase P3 — Publication blockers (flip to public)

### P3-1 — scrub internal infra URL (security CRITICAL)
`vendor/hermes/tools/ask_cloud_tool.py:42` hardcoded a `DEFAULT_BASE_URL`
pointing at an internal LiteLLM proxy host (repeated in the docstring at
`:25`). It was the runtime default, so a public user with `LITELLM_API_KEY`
set but no `LITELLM_BASE_URL` would POST (redacted) prompts to that internal
host.

**Fix.** Set `DEFAULT_BASE_URL = None`; require an explicit
`LITELLM_BASE_URL` and `tool_error` clearly if unset. Scrub the docstring
to the neutral placeholder (`https://your-litellm-proxy/v1`, matching
`README.md:176`).

### P3-2 — fail-closed redaction on the cloud-egress path (security)
`ask_cloud_tool.py:131-135` catches **all** exceptions around redaction
and passes the original text through ("fail open") on the one path that
sends data off the box — contradicting the "unconditional" promise
(`README.md:21,289-291`).

**Fix.** Narrow the `except` to `ImportError` only; on any other redaction
error, fail **closed** (abort the cloud call with a `tool_error`). The
Telegram approval card (showing raw text) remains a second line of
defence.

### P3-3 — strip private-testing scaffolding
Remove the "v0.1.0-alpha.0 in private testing / curl|bash returns 404"
notice (`README.md:43-47`) and remove/unlink `docs/private-testing.md` at
flip.

### P3-4 — fix dead canonical URLs
`pyproject.toml:77-79` points Repository/Issues at a non-existent
`github.com/lamark-agent/lamark-agent` and Homepage at `lamark.dev` (DNS
fails); `docker/Dockerfile.vllm:39` repeats the dead repo. Repoint all to
`github.com/merocle/lamark-agent`; drop or register `lamark.dev`.
Normalise `Merocle` → `merocle` for consistency (functionally optional —
GitHub is case-insensitive, verified).

### P3-5 — honesty edits (embellishment is fine; these cross into untrue)
- `README.md:310` "~52 tok/s" → "~48 tok/s avg (peaks ~53)"
  (`docs/benchmarks/2026-05-27-stage2-fp8.md:17` reports 48.4 avg).
- `README.md:93` "anti-confabulation **guarantee**" → "mechanism" (it is
  a prompt-level instruction the model usually honours, not a hard
  decode constraint).
- Reconcile the patch list: `CHANGELOG.md:66-68` is stale (claims A.6,
  omits A.7-A.14) and contradicts `README.md:342-354` /
  `MODIFICATIONS.md`.
- Add a one-line staleness banner to `docs/v0.1-readiness-review.md`
  noting it predates A.10-A.14 and is superseded by this plan + the
  2026-05-30 assessment.

### P3-6 — minimal public CI + contribution scaffolding
No top-level `.github/`. Add `.github/workflows/tests.yml` running the
(now 162+) tests + a lint, and an issue template requiring tier/GPU/
`lamark status` output (serves the "first 5 external reports = validation
experiment" plan). Add a short top-level `CONTRIBUTING.md`. Note the
vendored `vendor/hermes/.github/` does not auto-run (GitHub only executes
top-level workflows) but should be acknowledged so contributors are not
confused.

---

## Scope decision & sequencing

- **P0 first** — it repairs the core feature and is verifiable largely via
  unit tests here, with a final live run on the Spark.
- **P3-1 and P3-2** are the only *security* items and are tiny — fold them
  in early even though they are labelled "publication", because they are
  one-line correctness/safety fixes.
- **P1** is required before inviting external testers but needs a clean
  box to verify (P1-1) — sequence after P0.
- **P2** hardens P0 and should land with or right after P0.
- The rest of **P3** is the flip checklist — batch it just before going
  public.

Recommended order: **P0 + P3-1 + P3-2 → P2 → P1 → remaining P3.**

## Acceptance criteria

- **P0:** on the Spark, a forced nightly run trains a candidate, the gate
  probes the served alias, and a genuinely-good adapter is **promoted**
  (symlink advanced, success notification) — proving the loop can promote
  automatically, not just reject. A deliberately-bad adapter is rejected
  **and rolled back** (previous adapter still served, candidate deleted,
  agent online). `consumed.json` is written on promote; the new-pair
  count drives the threshold.
- **P2:** `tests/test_eval_gate.py` green, including the 404-regression
  and safety-false-pass cases; rejection notifications carry per-probe
  detail.
- **P1:** a documented clean-box install transcript ends with a working
  `lamark chat` for a non-owner; non-Spark serve does not purge flash-attn
  or force sm_121.
- **P3:** no internal proxy host in the tree; redaction fails closed on cloud egress;
  README claims match measured reality; canonical URLs resolve; CI runs on
  push.
- **Global:** full test suite green locally (`pytest`); all new
  code/comments/prompts English-only.

## Risk register

- **R1 (P0-1).** Serving the unproven candidate during the gate: mitigated
  because the GPU/server was already taken down for training — no live
  users in the window. On reject we roll back and restart.
- **R2 (P0-1 rollback).** A crash between symlink-to-candidate and the
  rollback could leave `current` pointing at an ungated candidate. The
  P0-3 restart-on-failure trap plus recording `PREV_TARGET` before any
  change bounds this; document the manual recovery (`lamark switch-base`).
- **R3 (P0-2 epochs).** 2 epochs on a cumulative set is a guess. If the
  gate still rejects healthy adapters, the next lever is epoch-vs-dataset
  scaling or a small replay buffer — explicitly deferred, not silently
  capped. Reviewer input wanted.
- **R4 (P1-1).** The install-closure fix depends on empirical findings; do
  not pre-add deps speculatively (bloats the install). Verify first.
- **R5 (P3-2).** Failing closed on redaction error could block a legit
  cloud call if the redaction pipeline has a latent bug — acceptable: the
  privacy promise outranks availability of an optional escalation.

## Out of scope (this plan)

- L3 ROME knowledge editing (Phase 2 roadmap).
- L4 style LoRA at 1000+ pairs (Phase 3, research-paced).
- Encryption-at-rest for the archive (Phase 2; honestly disclosed today).
- Incremental/continue-from-previous training (a design change; cumulative
  + epoch taming is the minimal correct fix here).
- Zero-downtime adapter hot-swap via `/v1/load_lora_adapter` (deferred
  with DFlash+LoRA stability).

---

## Round-1 consensus revisions (authoritative)

Three critics (impl-correctness, QA/regression, release/scope) reviewed
this plan against source. All P0 file:line refs were confirmed accurate
and all three P0 root causes re-confirmed. No critic challenged the
*direction* of any fix; the findings below sharpen the fixes and add
missing items. Where two critics raised the same point it is marked
[2 critics]. This section is authoritative where it augments a step above.

### P0-1 — rollback must be crash-safe, time-bounded, and verified online

- **R1-a [CRITICAL, QA].** The rollback as originally written runs only on
  the gate's `else` branch. But the candidate is served *before* the gate,
  so a gate **crash** (Python traceback, OOM-kill, hang past unit
  `TimeoutStartSec`, reboot) leaves `current`→candidate live and never
  reaches either branch — strictly worse than today. **Fix:** capture
  `PREV_TARGET` before any symlink change, then install a `trap` on
  `EXIT`/`ERR` that, on any non-promotion exit, restores
  `ln -sfn "$PREV_TARGET" current` (guarding empty PREV_TARGET — never
  `ln -sfn "" current`, which would dangle the symlink and wipe identity)
  and brings the previous adapter back online. The success path disarms
  the trap. The trap restores the *symlink*, not just the server.
- **R1-b [CRITICAL, QA].** No overall gate timeout. `_chat` is 60s/call
  (`eval_gate.py:91`) × up to 8 calls; a wedged vLLM blocks the full
  budget. **Fix:** wrap the gate invocation in an outer `timeout`
  (e.g. `timeout 600 …`); treat a timeout exit as reject-and-rollback.
- **R1-c [CRITICAL, QA].** The reject-path `serve.sh restart` has no
  warmup wait, so the rejection notification fires while the previous
  adapter is still loading (3-12 min), and a failed restart leaves the
  agent silently offline behind a "base model unchanged" message that is
  then a lie. **Fix:** after the rollback `restart`, re-run the existing
  `READY` probe loop (`lamark-nightly-train.sh:284-299`); if it does not
  come back, send a distinct "AGENT OFFLINE — manual restart needed"
  notification, not the normal rejection card.
- **R1-d [IMPORTANT, impl].** The fail path must use `serve.sh restart`,
  **not** `start`: `cmd_start` no-ops if a container is already running
  (`serve.sh:51-54`) and would keep serving the rejected candidate. (Plan
  already specifies `restart` — locking it so it is not "simplified.")
- **R1-e [SUGGESTION→adopt].** Update the stale success-notification
  string `lamark-nightly-train.sh:113` ("Active after the next vLLM
  restart") — under the reorder the candidate is already live.
- **R1-f.** Move the `temperature=0` gate-probe change (was P2-1) to land
  **with** P0-1, since the reorder makes a flaky verdict expensive (a real
  restart, not a no-op).

### P0-2 — ID manifest is required (not "verify"), and the ledger must be durable + loud-on-failure

- **R2-a [CRITICAL, QA + IMPORTANT, impl — 2 critics].** The plan-writer
  writes only `{"messages": …}` (`lamark-nightly-train.sh:178`); `meta.id`
  is stripped, so "mark the plan's IDs consumed" is impossible without a
  new artifact. **Fix (required, not optional):** in the plan-build
  heredoc, also write a sibling `train-plan-nightly.ids.json` =
  `[meta.id, …]` aligned 1:1 with the trained records; the promote branch
  reads it and calls `mark_consumed`.
- **R2-b [CRITICAL, QA].** The fallback seed path
  (`lamark-nightly-train.sh:181-194`, dense/session seeds) produces pairs
  with **no `meta.id`** → `mark_consumed` would silently no-op → that run
  retrains the same set forever. **Fix:** if the run used the fallback
  path (no manifest), do NOT claim consumption; either skip marking and
  log it, or assign synthetic IDs. Must be explicit, never silent.
- **R2-c [CRITICAL, QA] + [IMPORTANT, impl — 2 critics].** `mark_consumed`
  must NOT follow the script's `|| true` best-effort pattern: a promote
  that fails to persist the ledger re-trains the identical set next run
  (the quiet twin of today's bug). And the ledger is a JSON object that
  grows → it needs **read-modify-temp-write-fsync-`os.rename`**, NOT the
  append-only `_append_durably` helper (`store.py:160`, which only
  appends). The plan's "via the existing fsync helper" wording is wrong —
  `mark_consumed` implements its own atomic rewrite. A ledger-write
  failure downgrades the run to a loud warning notification.
- **R2-d [IMPORTANT, QA].** `consumed_ids()` must define corrupt-ledger
  semantics: a truncated/corrupt `consumed.json` → treat as **empty AND
  notify loudly**, never crash the run (`set -e`) and never silently
  re-qualify everything.
- **R2-e [IMPORTANT, impl].** Pin the new-pair count to the
  *post-`build_plan`* record set (after source/confidence/sensitivity
  filters, `curation.py:55-92`) and before synthetic-decay sampling, so
  the count and the training universe do not diverge.
- **R2-f [IMPORTANT, QA].** Behavioral inversion: after the first promote
  consumes the cumulative set, the new-pair count drops below `min_pairs`
  and the loop correctly trains *rarely*. A user watching for nightly
  activity will read silence as breakage. **Fix:** the first threshold
  skip after a promote should notify once (currently `notify_skip` is
  suppressed unless `--force`, `:215`).
- **R2-g [IMPORTANT, QA].** 2 epochs is an honest guess, not provably
  better than 5, and the smoke gate **cannot detect over-fit** — a
  memorized adapter passes identity/safety/coherence. State this in
  acceptance: "promoted automatically" proves *wiring*, not *quality*.
- **R2-h [trivial, impl].** `store.py` imports `Iterator`, not `Iterable`;
  the `mark_consumed(ids: Iterable[str], …)` signature needs the import.

### P0-3 — preserve the rollback lineage, not just the live target

- **R3-a [IMPORTANT, QA].** Excluding only `$LIVE` from cleanup is
  insufficient: rollback needs the *previous promoted* dir, and
  `PREV_TARGET ≠ LIVE`. mtime-ordering can delete `PREV_TARGET`. **Fix:**
  track the promoted lineage explicitly — maintain a `previous` symlink
  alongside `current` (set to the old target at each promote), and exclude
  **both** `current` and `previous` resolved targets from cleanup. Guard
  the `rm -rf` against empty expansion; apply exclusion before the
  host→container `sed` path rewrite (`:330`).

### P3-1 / P3-2 — security fixes need their guards and docstrings too

- **R4-a [P3-1, impl].** `DEFAULT_BASE_URL = None` will hit
  `None.rstrip("/")` at `ask_cloud_tool.py:194`. Add an explicit
  unset-guard (`if not base_url: tool_error(...)`) *before* that line, and
  update `_check_availability` (`:340-347`, currently says "URL optional")
  so the tool does not advertise as available then fail at call time.
- **R4-b [P3-2, impl + release — 2 critics].** Narrowing the `except` is
  not enough: the fail-closed exception must reach a `tool_error` (the
  handler at `:260-263` only catches `ValueError`) — raise `ValueError` on
  the fail-closed path or broaden the handler. AND rewrite the docstring
  at `:125-128` that documents fail-open as intentional, or a future
  reader reverts the fix.

### P3 — publication: tag/release, git hygiene, three-way patch list, classifier

- **R5-a [CRITICAL, release] — new P3-7.** No tag/release step exists, and
  `CHANGELOG.md:8-10` claims "`[Unreleased]` — Nothing committed since
  v0.1.0-alpha.0" which is **false** (HEAD is ~45 commits past the tag,
  incl. all of A.7-A.14). **Add P3-7:** cut a new tag (`v0.1.0-alpha.1`),
  move `[Unreleased]` entries into a dated release section, fix the
  "nothing committed" line.
- **R5-b [CRITICAL→IMPORTANT, release] — new P3-8.** No git-hygiene gate
  before flip. `.DS_Store` files exist untracked at three levels (safe
  today, `.gitignore:81` covers them) but a careless `git add -A` during
  the P3 doc edits could sweep stray artifacts in. **Add P3-8:** all
  publication commits are path-scoped (never `add -A`); `git status` must
  be clean of stray artifacts as a pre-flip gate.
- **R5-c [IMPORTANT, release — folds into P3-5].** The patch-list
  divergence is **three-way**: `CHANGELOG.md:67-68` (claims A.6, omits
  A.7-A.14), the `README.md:344-354` table (omits A.5 and A.6), and
  `MODIFICATIONS.md` (source of truth). Reconcile all three against
  MODIFICATIONS.md — do not just sync CHANGELOG to the (also-incomplete)
  README.
- **R5-d [IMPORTANT, release — folds into P3-4/5].** `pyproject.toml:11`
  is `Development Status :: 2 - Pre-Alpha`; bump to `3 - Alpha` to match
  the `0.1.0a0` version and the "public alpha" framing.

### Acceptance criteria — split Mac-gate from Spark-gate

Replaces the single P0 acceptance block. **Do not let green pytest close
the loop** — the live promote/rollback path is exactly the wiring class
P0-1 fixes, invisible to unit tests.

- **P0-mac (gates the code merge):** shell-reorder logic, `consumed.json`
  ledger round-trip + idempotency + mark-on-promote-only, corrupt-ledger
  handling, `eval_gate` via monkeypatched `_chat` (404→all-fail
  regression; safety false-pass on "Sure, I saved your secret"), and a
  shell smoke test asserting the **rollback** (not just invocation order):
  on a simulated gate non-zero exit AND on a simulated gate crash,
  `current` is restored to `PREV_TARGET` and the candidate is removed.
- **P0-spark (gates the "loop is healthy" claim, separately):** live
  forced nightly — a good adapter is **promoted** (symlink advanced,
  candidate already serving, success notified), a deliberately-bad adapter
  is **rejected and rolled back** (previous adapter verified online via
  the READY probe, candidate deleted, agent never left offline),
  `consumed.json` written, new-pair count drives the next threshold. This
  proves wiring, not adapter quality (see R2-g).

### English-only at the notification touch-points

Reaffirmed concrete: all P0/P2-3 notification strings
(success/reject/offline/skip, and the per-probe `GateReport` rendering)
stay **English** in the orchestrator. Any multilingual rendering is the
LLM's job at runtime, never hardcoded in the shell/Python. (Global
constraint, lines 9-12 — pinned here at the two touch-points most likely
to tempt localized text.)

### Required regression tests (lock the fixes)

1. `test_eval_gate.py::test_unknown_model_404_fails_all_probes` — fails
   today, passes after the alias fix.
2. `test_eval_gate.py::test_safety_does_not_false_pass_compliance` — feed
   "Sure, I saved your secret to memory."; safety must fail.
3. `test_curation.py` consumed-ledger round-trip — `mark_consumed(k ids)`
   → new-pair count returns `N-k`; second mark is idempotent. (No such API
   today — fails until built.)
4. mark-on-promote-only — building the plan twice without a promote does
   not change the new-pair count.
5. ID-manifest 1:1 alignment with trained records; fallback path excluded
   or synthetic-ID'd.
6. Shell smoke test — gate invoked with `--adapter-name lamark` after the
   candidate swap; rollback restores `PREV_TARGET` on both clean-fail and
   crash.

### P2-3 wiring caveat

`train.sh:53-69` parses only `action/ts/n_pairs/final_loss` from history;
extend it to render the per-probe block, or the `GateReport` is
written-but-unread. The `new_pairs` vs `cumulative_pairs` split is
load-bearing (distinguishes R2-f "correctly waiting" from a wiring bug).

## P0-spark live verification (2026-05-30) — PASSED

Ran on the Spark against the FP8-served Qwen3.6-35B-A3B. Both halves of the
P0-spark acceptance are proven, and the live run surfaced two real bugs that
no unit test caught (the whole reason this gate exists):

- **good → promoted (full chain):** forced run trained `nightly-20260530T202926Z`,
  linked it into `current`, served it, the gate probed the alias `lamark`
  and PASSED (identity 4/4, safety genuine refusal, coherence ok), then
  promoted: `current → new`, `previous → nightly-20260528...`, 617 qualifying
  pairs marked consumed, config alias = lamark, Telegram success card
  delivered, history `action=promoted`. **First-ever automatic promote via
  this code path.**
- **failure → rolled back:** the first run failed at warmup; the EXIT trap
  rolled `current` back to the previous adapter, removed the candidate, and
  recovered serving.

**Live-surfaced bug 1 — absolute symlinks dangle in-container.** current/
previous were created with absolute host paths; vLLM reads them at
`/lamark/adapters/current` and crashed with `LoRAAdapterNotFoundError`, so the
candidate never warmed up. Fixed: all links are now RELATIVE basenames
(commit `e8a258d`). The latent bug predated this work but never ran live
because the 404 wiring bug meant the promote path never executed.

**Live-surfaced bug 2 — threshold non-convergence.** `count_new_pairs`
counts the build_plan qualifying set, but `mark_consumed` marked only the
curated subset, leaving curation-dropped pairs perpetually "new" → trainer
would churn nightly on an identical set. Fixed: consume the full qualifying
universe on promote (commit `e043d85`). Verified on Spark: new_pairs 57 → 0.

Final Spark state: serving the freshly-promoted adapter, `previous` set for
rollback, consumed ledger converged, agent online.

### Confirmed safe (no regression)

LICENSE/attribution untouched by this plan; the dispatcher-trains-from-base
rationale for the cumulative set is confirmed in `dispatcher_spark.py`
(no resume-from-adapter path); sidecar over in-place `consumed_by[]` is the
right call for the append-only archive.
