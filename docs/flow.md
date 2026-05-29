# Lamark — System Flow Reference

> Complete data-flow and control-flow diagrams for every subsystem.
> Updated: 2026-05-29 — reflects three-process architecture, five-tier
> adaptation stack, MUSE/SkillOpt/LIFE-HARNESS skill lifecycle, and
> InferredBugs×paraphrase dataset pipeline.

---

## 1. Three-Process Overview

```
┌──────────────────────────────────────────────────────┐
│              Agent Runtime  (Rust)                    │
│  lamark chat / lamark gateway / lamark mcp-serve      │
│                                                        │
│  User/Gateway → HarnessStack → LLM → Tools → Trace    │
└──────────┬───────────────────────────────┬────────────┘
           │  HTTP (traces, memory,         │  HTTP
           │  skills, evals, adapters)      │  (context, skill retrieval)
           ▼                                ▼
┌──────────────────────┐      ┌─────────────────────────────────┐
│  Knowledge Base       │      │  Training Pipeline  (Python)    │
│  (Kotlin / Spring)    │      │  cron-driven, DGX Spark         │
│                       │      │                                  │
│  /agents /memory      │◄─────┤  collect → redact → paraphrase  │
│  /knowledge /graph    │      │  → transform → curate → quality  │
│  /search /eval_sets   │─────►│  → blend → SFT/DPO → eval       │
└──────────────────────┘      │  → promote / rollback            │
                               └─────────────────────────────────┘

Coupling:
  Agent ──► trace files on disk  (~/.lamark/traces/)
  Agent ◄──► KB over HTTP        (5 s timeout; SQLite spool fallback)
  Trainer ◄──► KB over HTTP      (pull traces, push adapter metadata)
  No shared database.
```

---

## 2. Agent Turn Flow

Every turn runs through the HarnessStack before and after LLM inference.

```
                    ┌────────────────────────────────────────────┐
                    │             Agent Turn Loop                  │
                    │                                              │
  User/Gateway ────►│  ┌─────────────────────────────────────┐    │
  (Op::UserInput)   │  │  1. Environment Contract Layer       │    │
                    │  │     • inject evolved ΔC into system  │    │
                    │  │       prompt (tool policies, domain  │    │
                    │  │       pitfalls, admissible actions)  │    │
                    │  └──────────────────┬──────────────────┘    │
                    │                     │                        │
                    │  ┌──────────────────▼──────────────────┐    │
                    │  │  2. Procedural Skill Layer           │    │
                    │  │     • BM25-retrieve top-k skills     │    │
                    │  │       from SkillStore                │    │
                    │  │     • inject SKILL.md + .memory.md   │    │
                    │  │       into system prompt             │    │
                    │  └──────────────────┬──────────────────┘    │
                    │                     │                        │
                    │         ┌───────────▼──────────┐            │
                    │         │   LLM inference       │            │
                    │         │   (frozen θ; provider │            │
                    │         │   → Qwen3.5-9B /      │            │
                    │         │     Nemotron-Nano /    │            │
                    │         │     Gemma4-27B)        │            │
                    │         └───────────┬──────────┘            │
                    │                     │ action at              │
                    │  ┌──────────────────▼──────────────────┐    │
                    │  │  3. Action Realization Layer         │    │
                    │  │     EXEC(at) or BLOCK(mt)            │    │
                    │  │     • validates tool name, args,     │    │
                    │  │       types, semantic constraints    │    │
                    │  │     • silent to user; model retries  │    │
                    │  │     (distinct from lamark-policy     │    │
                    │  │      which is authorization)         │    │
                    │  └──────────────────┬──────────────────┘    │
                    │                     │ EXEC only              │
                    │         ┌───────────▼──────────┐            │
                    │         │   Sandbox             │            │
                    │         │   (local/docker/      │            │
                    │         │    ssh/k8s)           │            │
                    │         └───────────┬──────────┘            │
                    │                     │ observation ot+1       │
                    │  ┌──────────────────▼──────────────────┐    │
                    │  │  4. Trajectory Regulation Layer      │    │
                    │  │     • detect repetition (≥3 same)   │    │
                    │  │     • detect oscillation (ABAB/6)   │    │
                    │  │     • budget warning (30% / 15%)     │    │
                    │  │     • None / Hint / Warning /        │    │
                    │  │       Directive escalation           │    │
                    │  └──────────────────┬──────────────────┘    │
                    │                     │                        │
                    │  ┌──────────────────▼──────────────────┐    │
                    │  │  Trace Recorder                      │    │
                    │  │  ~/.lamark/traces/<rollout_id>/      │    │
                    │  │  manifest.json + trace.jsonl +       │    │
                    │  │  payloads/                           │    │
                    │  └──────────────────┬──────────────────┘    │
                    │                     │ on TurnEnded           │
                    │                     ▼                        │
                    │     KB POST /agents/{id}/traces              │
                    └────────────────────────────────────────────┘

Hook bus fires at: PreToolUse, PostToolUse, UserPromptSubmit,
PermissionRequest, TurnStarted, TurnComplete, SessionStart/End.
SQ/EQ: all events flow to trace recorder, gateway, TUI simultaneously.
```

---

## 3. Skill Lifecycle (Three-Paper Stack)

```
                   User task arrives
                          │
              Does a matching skill exist?
             /                            \
           Yes                            No
            │                              │
            │                    ┌─────────▼──────────┐
            │                    │  MUSE: CREATE        │ arXiv:2605.27366
            │                    │  agent invokes       │
            │                    │  skill_create tool   │
            │                    │  → SKILL.md          │
            │                    │  → scripts/ (opt.)   │
            │                    │  → tests/ (opt.)     │
            │                    └─────────┬──────────┘
            │                              │
            │                    ┌─────────▼──────────┐
            │                    │  EVALUATE            │
            │                    │  run tests/ in       │
            │                    │  sandbox             │
            │                    │  pass → register     │
            │                    │  fail → patch+retry  │
            │                    └─────────┬──────────┘
            │                              │
            └────────────────┬─────────────┘
                             │
                    ┌────────▼───────────┐
                    │  Skill invoked      │
                    │  .memory.md updated │ (append-only per-skill
                    │  after each use     │  experience log)
                    └────────┬───────────┘
                             │
               ┌─────────────▼─────────────┐
               │  Weekly: SkillOpt Curator   │ arXiv:2605.23904
               │                             │
               │  for each epoch (×4):       │
               │    rollout batch (B=40)      │
               │    → failure/success split  │
               │    → reflect (Bm=8)         │
               │    → merge proposals        │
               │    → rank + clip (Lt=4)     │
               │    → candidate skill        │
               │    → validate (held-out)    │
               │      pass → best_skill.md   │
               │      fail → reject buffer   │
               │    slow/meta update (epoch) │
               │                             │
               │  Output: best_skill.md      │
               │  (300–2,000 tokens;         │
               │   1–4 accepted edits)        │
               └─────────────┬─────────────┘
                             │
               ┌─────────────▼─────────────┐
               │  Curator: Manage            │
               │  • merge overlapping skills │
               │  • prune unused/failing     │
               │  • archive (tarball backup) │
               └─────────────────────────────┘

Separately (weekly harness_evolve.py):

               Trace failures classified
               ┌─────────────────────────────┐
               │  LIFE-HARNESS evolution      │ arXiv:2605.22166
               │                             │
               │  CONTRACT_MISMATCH (33.3%)  │
               │  → evolve contract ΔC       │
               │                             │
               │  ACTION_REALIZATION (23.2%) │
               │  → evolve realization rules │
               │                             │
               │  TRAJECTORY_DEGENERATION    │
               │          (33.6%)            │
               │  → evolve regulation rules  │
               │                             │
               │  REASONING (9.9%)           │
               │  → route to SFT pipeline    │
               └─────────────────────────────┘
```

---

## 4. Failure Routing Decision Tree

```
Trace failure annotated
        │
        ├── ACTION_REALIZATION  (23.2%) ─────────────► realization_rules.toml
        │   tool call not executable                   (harness_evolve, weekly)
        │   (bad args, wrong fn name, JSON fail)
        │
        ├── CONTRACT_MISMATCH  (33.3%) ──────────────► contract_delta.md
        │   syntactically valid but violates           (harness_evolve, weekly)
        │   tool protocol or domain policy
        │
        ├── TRAJECTORY_DEGENERATION  (33.6%) ────────► regulation_rules.toml
        │   repetition, oscillation, stagnation,       (harness_evolve, weekly)
        │   budget exhaustion
        │
        └── REASONING  (9.9%) ──────────────────────► SFT blend
            correct interface, wrong inference          (nightly training)

⚠ Only the bottom 9.9% goes to SFT weights.
  The top 90.1% is fixed at zero weight cost via harness evolution.
```

---

## 5. Dataset Pipeline (git → SFT + RL)

```
Git repository
      │
      │ Phase 1 — Extract  (codebase_extract.py)
      │
      │  for each (parent, child) commit pair:
      │    checkout parent → run_analyzers() → parent_diagnostics
      │    checkout child  → run_analyzers() → child_diagnostics
      │    fixed = parent_diag - child_diag    (bugs present then gone)
      │    quality_filter (1-200 lines, ≤5 files, ≥20-char msg)
      │
      ▼
 bug_fix_pairs.jsonl
 { pair_id, repo, language, parent_sha, child_sha,
   diagnostics_fixed, buggy_context, diff, ewash_context }

      │
      │ Phase 2a — Enrich  (mr_enrich.py, optional)
      │  link commit → MR/PR via platform API
      │  add: issue_text, pr_description, iteration diffs,
      │       reviewer_comments, merged status
      │
      │ Phase 2b — Paraphrase  (paraphrase_pairs.py)
      │
      │  teacher: gpt-5.4-mini (configurable)
      │  n_variants: 3 per real pair
      │  → 1 real + 3 synthetic = 4× multiplier
      │
      │  each variant:
      │    same bug_code + fix pattern
      │    different variable names, context, style
      │    verify: re-run static analyzer on paraphrase
      │            → bug must be reproduced
      │    dedupe: embedding cosine < 0.92
      │
      ▼
 enriched_pairs.jsonl  (real + paraphrased, labelled by source)

      │
      │ MANDATORY: Redact (stage1_secrets → stage2_pii)
      │  Gitleaks + TruffleHog → Presidio + GLiNER
      │  type-preserving substitution, salted, consistent within doc
      │  (MUST happen before any frontier model sees the data)
      │
      ▼
 redacted_pairs.jsonl

      │
      ├── Phase 3 — Teacher Traces  (teacher_trace.py)   → SFT
      │
      │   teacher: gpt-5.4-mini (configurable)
      │   max_turns: 32, max_ctx: 64K
      │   for each pair: TASK_TEMPLATE → run teacher agent
      │   → Nemotron-Agentic-v1 trace
      │
      │   Three-stage filter:
      │   1. verifier determinism (3 runs, <60s)
      │   2. Docker env stability (<120s build)
      │   3. difficulty (10% ≤ ref_pass_rate ≤ 85%)
      │
      │   Output: codebase_sft.jsonl
      │   Feeds into: nightly blend (→ SFT)
      │
      └── Phase 4 — RL Tasks  (rl_task_build.py)         → GRPO
          instruction.md + Dockerfile + verifier.py
          three-stage filter (same as Phase 3)
          GRPO groups: N=8 trajectories per task
          Orchestration: Harbor + SkyRL
```

---

## 6. Nightly / Weekly / Monthly Adaptation Cycle

```
──────────────────────────────────────────────────────────────
MONDAY–FRIDAY  Nightly  T+0:00 .. T+9:00
──────────────────────────────────────────────────────────────

T+0:00  COLLECT
        lamark trace export --since=yesterday  →  /raw/lamark.jsonl
        KB pull memory.episodes                →  /raw/kb.jsonl
        git_mining --since=yesterday           →  /raw/git.jsonl
        pr_ingest                              →  /raw/pr.jsonl
        youtrack/jira_ingest                   →  /raw/issue.jsonl
        slack_ingest                           →  /raw/slack.jsonl
        codebase_extract --since=yesterday     →  /raw/codebase_pairs.jsonl

T+0:30  REDACT STAGE 1 (secrets)
        Gitleaks + TruffleHog + detect-secrets
        Any verified finding → drop sample + quarantine

T+0:45  PARAPHRASE  (new)
        paraphrase_pairs.py --n 3 --model gpt-5.4-mini
        → 4× multiplier on codebase_pairs
        → verifier confirms each paraphrase reproduces original bug

T+0:50  REDACT STAGE 2 (PII)
        Presidio + spaCy en_core_web_lg + GLiNER
        type-preserving substitution, salted, consistent IDs

T+1:00  TRANSFORM
        each source → Nemotron-Agentic-v1 JSONL
        codebase pairs (real + paraphrased) → teacher_trace.py

T+1:30  CURATE  (gated on teacher budget)
        OSS-Instruct seeding → two-judge consensus (≥4/5)
        → execution-based filter (Docker sandbox)

T+2:30  QUALITY + DEDUP + DECONTAMINATE
        PPL outlier (drop top+bottom 5%)
        MinHash-LSH dedup (Jaccard ≥ 0.85)
        13-gram decontamination against eval suite

T+2:55  CLASSIFY + BLEND
        annotate_failures() → route 90% to harness queue
        only REASONING failures enter SFT blend
        70/20/10/5: 65% new + 20% MSSR-replay + 10% anchor + 5% safety
        curriculum-within-pack (≥1 anchor + ≥1 replay per 4096-token pack)

T+3:00  PACK
        Nemotron: uv run nemotron nano3 data prep sft → Parquet
        Qwen3.5: Unsloth packs on-the-fly (packing=True)

T+3:30  TRAIN (SFT)
        Unsloth LoRA r=32 (Qwen3.5-9B / Qwen3.6 / Gemma4)  ~5 h
        Megatron-Bridge nano-v3 (Nemotron)                   ~5-6 h
        assistant_only_loss=True mandatory

T+8:30  EVAL-GATE
        MMLU-Pro-250, HumanEval, MBPP, SWE-Bench-Lite-50,
        BFCL v4, IFEval, IFBench, MT-Bench, internal gold,
        OpenThoughts-TBLite-100 (fast agent proxy)
        tool_call_compliance ≥ 0.995, Bonferroni-corrected

T+8:50  FORGETTING PROBE
        100 frozen examples per base
        1pp warn → 2pp/7d bump anchor → 3pp/7d suspend → 5pp rollback

T+9:00  PROMOTE or ROLLBACK
        vLLM hot-swap load_lora_adapter / unload_lora_adapter
        KB POST /agents/{id}/adapters (lineage)

──────────────────────────────────────────────────────────────
SATURDAY  Weekly  (Skill + Harness evolution)
──────────────────────────────────────────────────────────────

harness_evolve.py --since 7d
  → classify failures by type
  → evolve contract_delta, realization_rules, regulation_rules
  → new skills extracted from successes → KB

SkillOpt Curator (run_skillopt)
  → 4 epochs × B=40 rollouts × Bm=8 reflection
  → validation-gated edits (Lt=4 cosine)
  → output: best_skill.md per domain skill

MUSE Manage
  → merge overlapping skills
  → prune unused/failing (>30 days stale)
  → archive tarballs

──────────────────────────────────────────────────────────────
SUNDAY  Weekly DPO
──────────────────────────────────────────────────────────────

preference pairs from:
  • same-prompt reruns judged by two-model panel
  • PermissionDenied rejected branches
  • KB reinforce signal
DPOTrainer: lr=5e-6, β=0.1, 1 epoch
Same eval gate as SFT

──────────────────────────────────────────────────────────────
DAY 30  Monthly Merge
──────────────────────────────────────────────────────────────

merge_and_unload()          ← arithmetic LoRA delta → base weights
                              NO gradient steps
requantize (NVFP4 / AWQ-INT4)
reset_lora_delta()
recompute_ewc_fisher(anchor)
refresh_oss_instruct_seeds()
rotate_frontier_teacher()   ← Claude → GPT → Gemini → repeat
full_eval_sweep()
tag lamark-base-vYYYY.MM
KB upsert_release()
```

---

## 7. Knowledge Base Data Flow

```
Agent runtime
  └─► POST /agents/{id}/traces
       { rollout_id, manifest, reduced_state, conversation,
         outcome, metrics: {turn_count, tool_count, ms_total} }

  └─► POST /memory/facts          (long-term memory writes)
  └─► GET  /memory/search         (context retrieval)
  └─► POST /memory/user_profiles  (USER.md snapshot)
  └─► POST /knowledge/skills      (Curator-promoted skills)
  └─► POST /knowledge/datasets    (adapter lineage)
  └─► POST /agents/{id}/adapters  (promoted adapter metadata)
  └─► POST /agents/{id}/events    (AdapterPromoted / AdapterRejected)

Training pipeline
  └─► GET /agents/{id}/traces?since=...     (pull for training)
  └─► GET /memory/episodes?kind=reflexion   (Loop B lessons)
  └─► GET /knowledge/eval_sets?tag=gold     (gold set seeding)
  └─► GET /knowledge/eval_sets?tag=probe    (forgetting probe)
  └─► GET /agents/{id}/sections/{sid}/outcomes  (prompt section outcomes)

KB internal (knowledge-base service):
  Ingest: RAPTOR hierarchical + KG + pgvector dense indexing
  Query:  /search → hybrid (dense + sparse + graph)
  /graph  → skill + entity recommendation (drives Curator)
  /reinforce → RL-flavored re-weighting of memory samples
  RBAC: bearer token + per-project ACL
  Fallback: if KB unreachable → local SQLite spool (5s timeout)
```

---

## 8. Training Tier Decision Tree

```
Have a task or failure to address?
          │
          ▼
Does a SKILL exist that covers this?
   No → MUSE skill_create (training-free, immediate)
   Yes ↓

Does the skill perform well?
   No → SkillOpt Curator (weekly, bounded edits, validation-gated)
   Yes ↓

Is the failure an INTERFACE failure?  (classify from trace)
   ACTION_REALIZATION (23%) → harness_evolve realization rules
   CONTRACT_MISMATCH (33%)  → harness_evolve contract delta
   TRAJ. DEGENERATION (34%) → harness_evolve regulation rules
   Yes (any above) → DONE — no weight change needed

Is the failure a REASONING failure?  (~10%)
   Yes → route to SFT blend (nightly)

Is the behaviour an alignment issue?
   Yes → DPO preference pairs (weekly)

Is there RL environment + verifier available?
   Yes + SFT stable ≥30 nights → GRPO (v0.2+, weekly)

Never before: → CPT (Tier 0)
  Only if: raw domain corpus > 50 MB, not expressible as Q&A.
  Use BASE model, LoRA r=128 + embed/lm_head, ~5% pretrain replay.
  Skip in 99% of cases.

Never on single Spark: → Full weight FT (Tier 4)
  30B+ MoE optimizer states exceed 128 GB UMA.
  Monthly merge is arithmetic delta baking, not training.
```

---

## 9. Model × Hardware Decision Matrix

```
Hardware          VRAM    Primary model             Training
──────────────────────────────────────────────────────────────
DGX Spark         128 GB  Qwen3.6-35B-A3B (BF16)   Unsloth bf16 LoRA ~5h
                  unified Nemotron-3-Nano (BF16)    Megatron-Bridge ~5-6h
                          Current pivot: Qwen3.5-9B

RTX 4090/5090     24-32GB Qwen3.5-9B (QLoRA 4-bit) Unsloth QLoRA ~4h
RTX 3090/4080     24 GB   Qwen3.5-4B (QLoRA 4-bit) Unsloth QLoRA ~3h
RTX 3060 12GB     12 GB   Qwen3.5-4B (QLoRA 4-bit) Unsloth QLoRA ~3h
Apple M3 Pro 36GB         Gemma4-27B (MLX)          Unsloth LoRA + MLX
API-only          none    Any OpenAI-compat          n/a (no local training)

Inference quantization guidance:
  Qwen3.6 / Nemotron MoE: bf16 only on Spark (QLoRA OOMs at ~4% load)
  Qwen3.5-9B dense:  UD-Q4_K_XL or Q5_K_M  →  ~11 GB VRAM (serving)
  Qwen3.5-4B dense:  UD-Q5_K_XL or Q6_K    →  ~7-9 GB VRAM (serving)
  Qwen3.5-2B dense:  Q6_K or Q8_0           →  ~4-5 GB VRAM
  Qwen3.5-0.8B:      Q8_0 or BF16           →  <2 GB (4-bit loses 10 pp)
  All Qwen3.x: NEVER greedy (loops) — temp=0.7, top_p=0.8, top_k=20

Autonomous agent floor: Qwen3.5-4B and above only.
0.8B / 2B: classifiers and extractors inside deterministic harnesses.
```

---

## 10. Crate Dependency Graph (Rust)

```
lamark  (binary)
  ├── lamark-core          (types, traits, turn loop — no workspace deps)
  │     └── (nothing)
  ├── lamark-config        → lamark-core
  ├── lamark-providers     → lamark-core
  ├── lamark-tools         → lamark-core
  ├── lamark-sandbox       → lamark-core, lamark-tools
  ├── lamark-hooks         → lamark-core
  ├── lamark-policy        → lamark-core
  ├── lamark-skills        → lamark-core, lamark-kb-client
  ├── lamark-harness       → lamark-core, lamark-tools, lamark-skills
  │     (4 layers: Contract / Skill / Realization / Regulation)
  ├── lamark-trace         → lamark-core, lamark-hooks, lamark-kb-client
  ├── lamark-prompt        → lamark-core, lamark-skills, lamark-harness
  ├── lamark-cache         → lamark-core
  ├── lamark-memory        → lamark-core, lamark-kb-client
  ├── lamark-kb-client     → lamark-core
  ├── lamark-plugins       → lamark-core, lamark-hooks
  ├── lamark-coordinator   → lamark-core, lamark-hooks
  ├── lamark-mcp           → lamark-core, lamark-tools
  ├── lamark-acp           → lamark-core
  ├── lamark-gateway       → lamark-core, lamark-hooks, lamark-providers
  ├── lamark-protocol      → lamark-core
  ├── lamark-webui         → lamark-core, lamark-providers
  ├── lamark-remote        → lamark-core, lamark-protocol
  └── lamark-test-utils    → all above (dev-deps only)

Hard rules:
  lamark-core depends on nothing in this workspace.
  No peer imports (providers does NOT import tools; both import core).
  lamark binary wires all crates together at startup.
```

---

## 11. Skill Artifact Layout

```
~/.lamark/skills/<skill-name>/
├── SKILL.md          ← YAML frontmatter (name, description) + markdown body
│                        When to use / Core principles / Workflow
│                        (Anthropic Agent Skills format — cross-agent portable)
├── .memory.md        ← append-only per-skill experience log
│                        timestamp + agent-written failure modes, edge cases
│                        EXCLUDED from cross-agent transfers (per-agent only)
├── scripts/          ← optional executable code (Python, shell)
├── tests/            ← optional pytest suite (MUSE: mandatory for registration)
├── resources/        ← optional auxiliary data files
└── references/       ← optional reference documentation

Catalog indexing (two-stage):
  Stage 1: name + description only (~5-10K tokens for 100 skills)
           injected into system prompt at session start
  Stage 2: full SKILL.md + .memory.md loaded via read_skill tool
           only when agent decides to invoke the skill

Registration gate (MUSE):
  ALL tests/ must pass in sandbox before skill is added to SkillStore.
  Failed tests → agent patches → re-runs → register on pass.

SkillOpt output: best_skill.md (300-2,000 tokens, 1-4 accepted edits)
  replaces the previous SKILL.md after weekly Curator validation.
```

---

## 12. Trace Bundle Format

```
~/.lamark/traces/<rollout_id>/
├── manifest.json
│   { rollout_id, root_thread_id, started_at, ended_at,
│     schema_version, agent_version }
├── trace.jsonl           ← one event per line (append-only)
│   { seq, wall_time_unix_ms, thread_id, turn_id, kind,
│     payload_ref: "payloads/inference-0001-req.json" }
└── payloads/
    ├── inference-0001-req.json
    ├── inference-0001-resp.json
    ├── tool-0001-in.json
    └── ...

Event kinds (union of Codex + Lamark):
  RolloutStarted / ThreadStarted / TurnStarted / TurnComplete
  InferenceStarted / InferenceCompleted / InferenceFailed
  ToolCallStarted / ToolCallEnded / ExecCommandBegin / ExecOutput
  PermissionRequest / PermissionGranted / PermissionDenied
  UserPromptSubmit / SkillInvoked / CuratorRun
  GatewayMessageIn / GatewayMessageOut / CompactionRequestStarted

Reducer (lamark trace reduce) emits:
  state.json         ← Codex-style reduced graph
  conversation.jsonl ← Nemotron-Agentic-v1 format (one rollout = one line)
```

---

*All diagrams reflect the current implementation target as of 2026-05-29.*
*See `docs/plan/` for layer-by-layer Rust implementation plans.*
*See `docs/plan/10-training-pipeline.md` for the full nightly runbook.*
*See `docs/plan/10d-skillopt-life-harness.md` for SkillOpt / LIFE-HARNESS / MUSE specs.*
