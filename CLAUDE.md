# Lamark — agent guide

Self-improving local agent. Rust runtime (`agent/`), Codex-style trace bundles + provider trait, integrated with sibling `../knowledge-base` for canonical memory + dataset storage. Python training pipeline (`learning/`).

**Reference clones:** `~/.cache/lamark/vendor/{hermes-agent,claude-code,codex}`
**Sibling project:** `../knowledge-base` (Kotlin/Spring; exposes `/knowledge /search /graph /memory /agents` API)

See [`docs/specs/`](./docs/specs/) for the full spec (start at `00-overview.md`) and [`docs/plan/`](./docs/plan/) for the layer-by-layer implementation plan.

---

## Project layout

```
lamark-agent/                         # monorepo root
├── CLAUDE.md  (← AGENTS.md symlinks here)
├── LICENSE
├── README.md
├── agent/                            # Rust agent workspace
│   ├── Cargo.toml                    # workspace root (run cargo/just from here)
│   ├── rust-toolchain.toml
│   ├── rustfmt.toml
│   ├── deny.toml
│   ├── justfile
│   ├── .cargo/config.toml
│   └── crates/
│       ├── lamark/                   # binary (CLI + bootstrap + TUI)
│       ├── lamark-core/              # types, traits, shared primitives
│       ├── lamark-config/            # layered config
│       ├── lamark-providers/         # ModelProvider trait + all impls
│       ├── lamark-tools/             # tool registry + built-ins
│       ├── lamark-sandbox/           # Sandbox trait + backends (local/docker/ssh/k8s)
│       ├── lamark-hooks/             # hook bus
│       ├── lamark-trace/             # recorder + reducer + KB upload
│       ├── lamark-prompt/            # composer + section model
│       ├── lamark-cache/             # cache_control + prefix-hash strategies
│       ├── lamark-memory/            # memory trait + Honcho/Mem0/Hindsight/SQLite
│       ├── lamark-kb-client/         # knowledge-base HTTP client
│       ├── lamark-skills/            # skill loader + frontmatter + Curator
│       ├── lamark-plugins/           # plugin host (wasmtime + libloading)
│       ├── lamark-gateway/           # gateway core + adapters (telegram/slack/discord)
│       ├── lamark-mcp/               # MCP client + server (rmcp-based)
│       ├── lamark-acp/               # ACP registry + adapter
│       ├── lamark-coordinator/       # coordinator + Kanban inter-agent protocol
│       ├── lamark-workflow/          # dynamic workflow engine: plan-then-execute with parallel subagents
│       ├── lamark-harness/           # four lifecycle layers: Contract/Skill/Realization/Regulation
│       ├── lamark-policy/            # Allow|Prompt|Forbidden DSL parser + evaluator
│       ├── lamark-protocol/          # shared wire types (schemars-derived)
│       ├── lamark-webui/             # axum server + embedded SvelteKit SPA
│       ├── lamark-remote/            # tonic gRPC + WS server + Rust client
│       └── lamark-test-utils/        # fixtures, recorded tapes, ratatui assertions
├── docs/
│   ├── specs/                        # deep spec (what/why; split from the old SPEC.md)
│   ├── decisions/                    # ADRs (NNNN-kebab-case.md)
│   ├── plan/                         # layer-by-layer Rust implementation plans
│   ├── scenarios/                    # use-case scenarios + _audit/
│   └── python/                       # Python training pipeline docs
└── learning/                         # Python training pipeline (LoRA/SFT/DPO)
    ├── pyproject.toml                # lamark-train package
    ├── src/lamark/                   # train/, redaction/, archive/, hardware.py
    ├── tests/
    ├── vendor/hermes/                # Vendored Hermes Agent MIT (Nous Research)
    ├── docker/Dockerfile.vllm
    └── scripts/                      # nightly-train, model-registry, vllm_server, etc.
```

Crate names are prefixed with `lamark-`. For example, the `core` crate is `lamark-core`.

---

## Crate dependency rules

Hard rules; enforced with `cargo deny` and CI:

1. **`lamark-core` depends on nothing else in the workspace.** Types, traits, error types, IDs only.
2. **No upper layer imports from a peer.** `lamark-providers` does not import `lamark-tools`. Both import `lamark-core`. The binary (`lamark`) wires them together.
3. **No layer logs through `println!` or `eprintln!`.** Everything goes through `tracing`.
4. **No layer calls `std::process::exit`.** Return `Result`; the binary decides the exit code.
5. **No layer spawns its own tokio runtime.** They expect to be called from within `#[tokio::main]`.

---

## Rust conventions

- When using `format!` and you can inline variables into `{}`, always do that.
- Always collapse if statements (Clippy `collapsible_if`).
- Always inline `format!` args when possible (Clippy `uninlined_format_args`).
- Use method references over closures when possible (Clippy `redundant_closure_for_method_calls`).
- Avoid bool or ambiguous `Option` parameters that force callers to write hard-to-read code like `foo(false)` or `bar(None)`. Prefer enums, named methods, newtypes, or other idiomatic Rust API shapes. When you cannot change the API, follow the argument-comment convention: use an exact `/*param_name*/` comment before opaque positional literals (`None`, booleans, numeric literals). Do not add these comments for string or char literals.
- Make `match` statements exhaustive; avoid wildcard arms when possible.
- Newly added traits must include doc comments explaining their role and how implementations are expected to use them.
- Avoid both `#[async_trait]` and `#[allow(async_fn_in_trait)]`. Prefer native RPITIT trait methods with explicit `Send` bounds:
  ```rust
  fn foo(&self, ...) -> impl std::future::Future<Output = T> + Send;
  ```
  Implementations may use `async fn foo(&self, ...) -> T` when they satisfy that contract.
- Prefer private modules and explicitly exported public crate API.
- Do not create small helper methods referenced only once.
- Avoid large modules: target under 500 LoC (excluding tests); if a file exceeds ~800 LoC, add new functionality in a new module instead of growing the existing file.
- When extracting code from a large module, move the related tests and type docs to the new module so invariants stay close to the code that owns them.
- When writing tests, prefer comparing the equality of entire objects over field-by-field assertions.

---

## Build and test commands

All Rust commands run from the `agent/` directory (the Cargo workspace root).

Install the task runner if it isn't already available: `cargo install just`.

| Task | Command (run from `agent/`) |
|---|---|
| Format (run after any Rust changes) | `just fmt` |
| Lint + fix | `just fix -p <crate-name>` |
| Test a specific crate | `just test -p lamark-<name>` |
| Full test suite | `just test` (ask the user before running this) |
| Build docs | `just docs` |

**Do not run `cargo test` directly.** Use `just test` so test execution follows repo defaults.

Run `just fmt` automatically after finishing Rust code changes; do not ask for approval to run it.

Before finalizing a large change, run `just fix -p <crate-name>` to fix linter issues. Prefer scoping with `-p` to avoid slow workspace-wide Clippy builds; only run `just fix` without `-p` when you changed shared crates. Do not re-run tests after `fix` or `fmt`.

When running Rust commands, be patient — Rust lock contention can make execution slow. Never try to kill them using the PID.

---

## Testing conventions

### Snapshot tests

This project uses `insta` for snapshot tests, especially in the TUI and prompt-composer crates.

Any change that affects user-visible output must include corresponding `insta` snapshot coverage. Review and accept snapshot updates as part of the change.

When output changes intentionally:
```sh
just test -p lamark-<name>           # generate updated snapshots
cargo insta pending-snapshots -p lamark-<name>   # check what's pending
cargo insta accept -p lamark-<name>  # accept all new snapshots in this crate
```

### Test assertions

- Use `pretty_assertions::assert_eq` for clearer diffs. Import at the top of the test module if not already present.
- Prefer deep equality comparisons: `assert_eq!()` on entire objects, not individual fields.
- Avoid mutating the process environment in tests; pass environment-derived flags from above.

### Provider integration tests

Use `wiremock` for HTTP mocking in provider tests. Build SSE payloads with the provided constructors and keep tests focused on structured payloads, not raw JSON.

---

## Planning discipline

- Skim `README.md` for the user-facing picture, then `docs/specs/` for the full design, then the relevant `docs/plan/NN-*.md` for the layer you're touching.
- For non-trivial implementation work, write the regression test first and confirm it fails (RED) before writing the fix (GREEN). A single commit "test + fix" is suspicious; prefer two commits.
- Don't add abstractions for hypothetical future needs. Three similar lines is better than a premature framework.
- Don't add error handling, fallbacks, or validation for scenarios that can't happen. Trust internal code and framework guarantees.

---

## Code that must not be added to `lamark-core`

Over time, `lamark-core` risks becoming bloated. **Resist adding code to `lamark-core`.** Before adding, consider whether there is an existing crate that is the right home, or whether it is time to introduce a new crate for the new concept.

---

## Reference repos (read-only; never link)

| Source | Path | Role |
|---|---|---|
| openai/codex (Apache 2.0) | `~/.cache/lamark/vendor/codex` | Rust north star: turn loop, provider trait, trace format, SQ/EQ pattern, MCP. |
| NousResearch/hermes-agent (MIT) | `~/.cache/lamark/vendor/hermes-agent` | Architecture model; re-implement in Rust. v0.14.0 "Foundation Release" (May 2026). Key files: `run_agent.py` (AIAgent core, single entrypoint for CLI/gateway/MCP/ACP), `prompt_builder.py` + `prompt_caching.py` (prompt assembly + Anthropic cache breakpoints), `context_compressor.py` (two-layer compression: 85% rough pre-gate + 50% in-loop), `tools/registry.py` (~70 tools, ~28 toolsets, ThreadPoolExecutor up to 8 parallel workers). Multi-agent Kanban + `/goal` Ralph-loop (v0.13.0), Curator 7-day skill cycle, Atropos RL trajectory export (`batch_runner.py` + `trajectory_compressor.py`), LSP semantic diagnostics on every file write (v0.14.0). Python copy vendored at `learning/vendor/hermes/`. |
| claude-code mirror | `~/.cache/lamark/vendor/claude-code` | Hook taxonomy + prompt composition design reference (licensing risk — read interface only, never copy code). |

These repos are developer conveniences. CI does not depend on them.

---

## Key architectural points

- **SQ/EQ pattern:** `Op` (submit queue) in, `Event` (event queue) out. The trace recorder, gateway adapters, and TUI all consume the same event stream — no direct injection.
- **Trace bundle format:** `~/.lamark/traces/<rollout_id>/manifest.json` + `trace.jsonl` + `payloads/`. Adopted from Codex's rollout-trace format. Large bodies go in `payloads/`; `trace.jsonl` stays cheap to scan.
- **ModelProvider trait:** provider choice is config, not code. `LocalOpenAICompat` talks to vLLM/Ollama/llama.cpp/SGLang; `AnthropicCompat` carries `cache_control` breakpoints.
- **Memory:** all persistent data lives in `../knowledge-base` over HTTP. No shared DB. KB calls have a 5 s timeout; writes are fire-and-forget with a local SQLite spool; reads degrade gracefully to the SQLite fallback when KB is down.
- **Policy:** every shell- and write-class tool fires `PermissionRequest`; default is `Decision::Prompt`; declarative `Allow|Prompt|Forbidden` rules live in `agent/crates/lamark/policy.toml`.
- **Forbidden deps:** GPL/AGPL transitive. Enforced by `cargo deny`. (TruffleHog is AGPL but runs out-of-process in the training pipeline — that's fine.)
- **MSRV:** Rust 1.94.1 (always use latest stable). Edition 2024.
- **Dependency versions:** always use the latest stable version of every crate. Do not pin to an older version unless a specific incompatibility is documented with a comment in `Cargo.toml`. When adding or updating a dependency, check crates.io for the current latest and use that version.
- **Nemotron-3-Nano** is a Mamba-2 / Transformer hybrid MoE: 52 layers (23 MoE + 23 Mamba-2 + 6 GQA attention), NoPE (no positional encodings). Mamba-2 layers have fixed recurrent state — the KV cache grows only from the 6 attention layers, making it ~3× smaller than a comparable pure-Transformer MoE at the same context length. The full BF16 model (~62 GB) fits DGX Spark's 128 GB unified memory with 256K+ context comfortable. Reasoning toggle: `enable_thinking=True/False` in chat template; `<think>` = token-id 12, `</think>` = token-id 13. Do not tune the router (rule 6). Recommended inference params: `temperature=1.0, top_p=1.0` (thinking on), `temperature=0.6, top_p=0.95` (tool calls), greedy (thinking off). Production DGX Spark serving: `avarok/vllm-dgx-spark:v11` + NVFP4 quant `cybermotaz/nemotron3-nano-nvfp4-w4a16` with `--kv-cache-dtype fp8 --gpu-memory-utilization 0.85` (~65 tok/s single-stream / 167 tok/s @ concurrency 10). Set `--gpu-memory-utilization` 0.70–0.85 on Spark; higher starves the OS page cache.
- **Qwen3.5 family** (Feb 2026, Apache 2.0) is a new architecture — **hybrid Gated DeltaNet (SSM) + Gated Attention + sparse MoE**, natively multimodal, 262K native context (extensible to 1M), 201 languages. Dense branch: 0.8B / 2B / 4B / 9B. MoE branch: 35B-A3B (`Qwen3.6-35B-A3B`, `qwen3_5_moe` arch ID), 122B, 397B. **LoRA target modules differ** from pure-Transformer — DeltaNet layers have different projection names; verify Unsloth `FastModel.from_pretrained` accepts the model before committing a training run. Qwen3.5-0.8B and -2B are not autonomous agents (classifiers/extractors only). Qwen3.5-4B is the practical autonomous-agent floor. Qwen3.5-9B is a full-capability agent comparable to Nemotron-Nano scale. Never greedy on any Qwen3.5 model.
- **Qwen3.6-35B-A3B** is the MoE branch of the Qwen3.5 family (`qwen3_5_moe` arch ID). Load BF16 on DGX Spark via the kreuzhofer eager-load patch (`patched_load_shard` with `posix_fadvise POSIX_FADV_DONTNEED`) to bypass UMA double-allocation OOM at ~66% of weight load. Production serving: `vllm serve --enable-lora --enable-mixed-moe-lora-format --max-loras 4`; per-request adapter selection via `"model": "adapter-name"` in the OpenAI request body. **Never use greedy decoding with Qwen3** — they loop at greedy; use `temperature=0.7, top_p=0.8, top_k=20` (tool loops / thinking off) or `temperature=0.6, top_p=0.95, top_k=20` (reasoning / thinking on).
- **Data mixing (70/20/10/5 rule):** every nightly training blend is 65% new task data + 20% rolling 30-day replay (MSSR-weighted by forgetting risk) + 10% General Anchor (FROZEN, quarterly refresh only) + 5% Safety Anchor (FROZEN). Curriculum-within-pack: every 4096-token pack must contain ≥1 anchor sample + ≥1 replay sample. See `docs/plan/10-training-pipeline.md` and `setup-guide.md` §3.5 for the full recipe.
- **Dynamic workflow engine** (`lamark-workflow`) — triggered by the word "workflow" in a user prompt (or complexity threshold). Model writes a `WorkflowPlan` JSON in one structured-output call (title + phases + tasks + dependencies). `WorkflowEngine` executes deterministically: `parallel` phases spawn up to 16 subagents simultaneously; `pipeline` passes results forward; `sequential` runs in order. No further LLM planning calls during execution. Whole workflow traces link sub-rollouts under one `WorkflowId` for KB upload and training data. Training dataset: `docs/plan/10e-workflow-training-dataset.md` — ~6K synthetic decomposition + synthesis examples, 10–15% share of nightly SFT blend. Complement to Kanban (plan/05a): Kanban is reactive/ad-hoc; workflows are proactive/pre-planned.
- **MUSE-Autoskill skill lifecycle** (arXiv:2605.27366, ByteDance/RIT): three-paper combined loop — MUSE **creates** skills from experience (agent-triggered `skill_create` tool; unit tests gate registration; `.memory.md` accumulates per-skill experience); SkillOpt **optimizes** existing skills; LIFE-HARNESS **fixes** interface failures. MUSE is training-free; skills mirror Anthropic Agent Skills format (directly compatible with `lamark-skills`). Key numbers: 87.94% on tasks with generated skills (exceeds human-skill ceiling 68.40%); -20% tokens/-37% latency after ~3 reuses; cross-agent transfer closes 79% of gap to human-skill performance. `.memory.md` is excluded from cross-agent transfers — experience is per-agent, skill code is universal.
- **90% of agent failures are NOT reasoning failures** (LIFE-HARNESS, arXiv:2605.22166, across 900 annotated trajectories): Contract mismatch 33.3%, Trajectory degeneration 33.6%, Action realization 23.2%, Reasoning only 9.9%. SFT/GRPO address 9.9%. The `lamark-harness` four-layer stack addresses 90%. This changes training priority: fix the harness first, SFT second. Filter trace failures by type before blending — only REASONING failures belong in the SFT bucket.
- **Skills before weights** (SkillOpt, arXiv:2605.23904): SkillOpt adds +23.5 pp avg on frozen GPT-5.5 without any weight changes. Tested on Qwen3.5-4B and Qwen3.6-35B-A3B. The Curator is now a SkillOpt loop (rollout→reflect→bounded edit→validate→reject buffer→slow update). Skills transfer cross-model and cross-harness. Deployed artifact: best_skill.md, 300–2,000 tokens, 1–4 accepted edits.
- **Action Realization vs Policy** — `lamark-harness/realization.rs` validates tool calls before sandbox execution (EXEC|BLOCK); `lamark-policy` grants/prompts user permission. These are orthogonal. Realization is silent to the user; Policy shows a permission dialog. Never conflate them.
- **Codebase extraction (InferredBugs × OpenThoughts):** `codebase_extract.py` runs the project's static analyzer (cargo check / mypy / tsc / go vet / …) on consecutive commit pairs, extracts fixed bugs as teacher-traced SFT examples and Docker+pytest RL task triplets. Keyword search on commit messages misses 41–97% of such fixes. See `docs/plan/10c-dataset-from-codebase.md` for the full spec.
- **OpenThoughts-Agent key findings:** (a) Teacher model family matters more than model size — GLM-4.6 gave ~2× downstream improvement on Terminal-Bench vs GPT-family teachers; rotate teacher quarterly to prevent distribution collapse. (b) RL gives modest incremental gain: SFT-only 16.1% → SFT+RL 17.3% (+1.2 pp) on TB-Dev; SFT data quality is the primary lever. (c) ~15K SFT traces was sufficient for Qwen3-8B to reach 15.7% SWE-Bench Verified (vs 0.7% baseline) — quality filter aggressively, don't pad. (d) RL task triplet format: instruction.md + Dockerfile (Ubuntu, repo at buggy commit) + verifier.py (pytest that re-runs analyzer). (e) Three-stage RL filter: bad verifier → env stability → difficulty (discard tasks with < 10% or > 85% reference pass rate). See `open-thoughts/OpenThoughts-TBLite` (100 tasks, r=0.911 with Terminal-Bench 2.0) as fast eval proxy.
- **Nemotron-3-Super (120B-A12B):** larger sibling of Nano. Three architectural additions not in Nano: (1) **NVFP4 pre-training** (unlike Nano which used BF16; 4× inference speedup on B200 vs FP8 on H100); (2) **Multi-Token Prediction** — predicts 3 future tokens simultaneously (up to 3× structured-generation wall-clock speedup); (3) **Latent MoE** — tokens projected into compressed low-rank latent space (4× more effective experts for same compute). Not fine-tunable on single Spark (120B optimizer states exceed 128 GB UMA). Use as a teacher model or hosted endpoint for synthetic data generation.
- **Nemotron eval suite (official benchmarks):** BFCL v4 (53.8%), LiveCodeBench v6 (68.3%), MMLU-Pro (78.3%), GPQA Diamond (73.0%), AIME 2025 (89.1%), SciCode (33.3%), IFBench (71.5%), HLE (10.6%). Run via NeMo Evaluator SDK (`github.com/NVIDIA-NeMo/Evaluator`) + NeMo Skills. Use `nvcr.io/nvidia/nemo:25.11.nemotron_3_nano` container. These are the baseline scores to beat with each nightly adapter.
- **Qwen datasets:** `Qwen/DeepPlanning` (1K–10K; planning with proactive API calls; Apache 2.0; arXiv:2601.18137) maps directly to Lamark's tool-use loop — use for cold-start SFT. `Qwen/RationaleRM` (22K+1K; atomic-rationale preference pairs; CC BY 4.0; RM-Bench 87.1%; arXiv:2602.04649) is the highest-quality preference dataset for DPO/reward model training. Both ingest as Nemotron-Agentic-v1 via `transform/trace_to_messages.py`.

---

## TUI style (ratatui)

- Prefer Stylize helpers: `.dim()`, `.bold()`, `.cyan()`, `.italic()`, `.underlined()` over manual `Style`.
- Simple spans: use `"text".into()`.
- Styled spans: use `"text".red()`, `"text".green()`, `"text".magenta()`, etc.
- Avoid hardcoded `.white()`; prefer the default foreground.
- Chain helpers for readability: `url.cyan().underlined()`.
- Use `textwrap::wrap` to wrap plain strings. For ratatui `Line` wrapping, use helpers in `lamark-tui` (if/when that crate exists).

---

## Documentation

Keep architecture decision records in `docs/decisions/` with the `NNNN-kebab-case-title.md` naming convention. Layer-by-layer implementation plans live in `docs/plan/`. Use-case scenarios live in `docs/scenarios/`. Python training pipeline docs live in `docs/python/`.

---

## What `learning/` is for

One thing only: the training pipeline. It is a separate process. The Rust agent never imports it. Cron runs it. It reads from `~/.lamark/traces/` (trace bundles produced by the Rust agent) and writes adapters to `~/.lamark/adapters/`. It talks to knowledge-base through the HTTP API.

**Nightly cycle (T+0..T+9h) — high-level steps:**

| Step | What happens |
|---|---|
| T+0:00 Collect | `lamark trace export --since=yesterday` + KB pull + git/PR/YouTrack/Slack connectors → `/raw/*.jsonl`; `codebase_extract --since=yesterday` → `/raw/codebase_pairs.jsonl` |
| T+0:30 Redact stage 1 | Gitleaks + TruffleHog (`--no-verification` air-gapped) + detect-secrets. Any verified finding blocks the sample. |
| T+0:45 Redact stage 2 | Presidio (`presidio-analyzer` + spaCy `en_core_web_lg`) + GLiNER. Type-preserving substitution (`<EMAIL_3>`, `<API_KEY_OPENAI_1>`, …), salted, consistent IDs within each document, reset across documents. |
| T+0:45 Paraphrase | `paraphrase_pairs.py --n 3 --model gpt-5.4-mini` — generates 3 synthetic code variants per real bug-fix pair (4× multiplier); verifies each paraphrase reproduces the original bug via static analyzer. |
| T+1:00 Transform | Source → Nemotron-Agentic-v1 JSONL. Lamark traces: reducer already emitted `conversation.jsonl`; consume directly. Git/PR/issue/Slack: `transform/` converters. Real + paraphrased codebase pairs both transformed. |
| T+1:30 Curate | Post-redaction only (safe to send to frontier models). OSS-Instruct seeding → two-judge consensus (Claude+GPT, both ≥ 4/5) → execution-based filter for code (Docker sandbox, `mypy`/`cargo check`/`tsc`). |
| T+2:30 Quality | PPL outlier (drop top+bottom 5% via base model), MinHash-LSH dedup (Jaccard ≥ 0.85), 13-gram decontamination against eval suite, language balance, length cap (>16K tokens dropped unless long-context), residual PII rescan. |
| T+2:55 Blend | 70/20/10/5 rule + MSSR-weighted replay + curriculum-within-pack. See `build_nightly_blend()` in `data-pipeline/build_blend.py`. |
| T+3:00 Pack | Nemotron path: `uv run nemotron nano3 data prep sft` → packed Parquet (4096-token bins, loss-mask rolled). Qwen3/Gemma4 path: Unsloth packs on-the-fly with `packing=True`. |
| T+3:30 Train | Unsloth LoRA (Qwen3/Gemma4, ~5 h); or Megatron-Bridge `nano-v3` (Nemotron, ~5–6 h). |
| T+8:30 Eval-gate | MMLU-Pro-250, HumanEval, MBPP, SWE-Bench-Lite-50, BFCL v4, IFEval, MT-Bench, Arena-Hard-100, internal gold set. `tool_call_compliance ≥ 0.995` strict. Bonferroni-corrected. |
| T+8:50 Forgetting probe | 100 frozen examples per base model, judge-scored. 1pp single-night → warn. 2pp/7d → auto-bump anchor 10%→15%. 3pp/7d → suspend nightly cycles. 5pp anywhere → rollback to last weekly snapshot. |
| T+9:00 Promote/rollback | vLLM hot-swap (`load_lora_adapter` / `unload_lora_adapter`). POST adapter metadata + event to knowledge-base. |

**Weekly** (Sundays): DPO preference pairs from same-prompt reruns, PermissionDenied rejected branches, two-judge re-scoring.

**Monthly** (day 30): `merge_and_unload` → requantize (NVFP4/AWQ-INT4) → reset LoRA delta → recompute EWC Fisher → rotate frontier teacher (Claude → GPT → Gemini) → full eval sweep → tag `lamark-base-vYYYY.MM`.

**General Anchor sources** (frozen; build once quarterly, never rotate nightly):

```python
anchor = {
    "tulu3_sft":         (sample=10_000, source="allenai/tulu-3-sft-mixture"),
    "openhermes_25":     (sample=5_000,  source="teknium/OpenHermes-2.5"),
    "helpsteer3":        (sample=3_000,  source="nvidia/HelpSteer3"),
    "ifeval_train":      (sample=1_000,  source="google/IFEval-train"),
    "your_gold_set":     (sample=2_000,  source="internal/curated_gold"),
    "math_anchor":       (sample=1_000,  source="nvidia/OpenMathReasoning"),
    "code_anchor":       (sample=2_000,  source="bigcode/the-stack-smol"),
    "general_chat":      (sample=2_000,  source="lmsys/lmsys-chat-1m"),
    # New anchors from dataset catalog expansion:
    "agentic_anchor":    (sample=2_000,  source="nvidia/Nemotron-SFT-Agentic-v2"),
    "tool_call_anchor":  (sample=1_000,  source="Salesforce/xlam-function-calling-60k"),
    "planning_anchor":   (sample=500,    source="Qwen/DeepPlanning"),
}
```

Install: `pip install -e learning/[spark]` (ML extras require Linux + CUDA). For dev without GPU: `pip install -e learning/`.

The `lamark-train` entry point is the Python training CLI. The Rust binary `lamark` is the primary agent command.

### Working SFT pipeline (validated on DGX Spark, Qwen3.5-9B)

The nightly cycle above is the target shape; the pieces that actually run **today** live in `learning/scripts/` + `learning/scripts/spark/` and were validated end-to-end (identity + knowledge + tool-catalog Q&A; gate PASS). Use these — don't write a fresh `train.py`.

- **Image:** `lamark/sft:26.01` (`learning/docker/Dockerfile.sft`) — NGC `pytorch:26.01` + **transformers 5.9** (loads `qwen3_5`) + TRL + peft + torchao 0.17 + causal-conv1d + **flash-linear-attention** (fast Qwen3.5 gated-DeltaNet kernels — without it the model is ~2× slower on a torch fallback). The EasyEdit/MEMIT image is **abandoned** (invariant 20).
- **Catalog source of truth:** `learning/data/tools.yaml` (63 tools, adopt/defer/drop) → feeds both `docs/specs/04-tooling-and-protocol.md` and the generator.
- **The loop:** edit facts / identity banks (`build_dataset.py`) / `tools.yaml` → `generate_tool_dataset.py {facts,qa,trajectories}` → `build_dataset.py --facts … --tool-facts … --tool-qa … --general general_base.jsonl --out-dir ~/.lamark/data` → a trainer → `spark/probe_gate.py` (scored identity/knowledge/tools/regression, exit 0 = PASS) + `spark/probe_compare.py` (adapter on/off) / `spark/probe_trajectory.py` (native tool-calls).
- **Trainers** (all bf16, instruct base `Qwen/Qwen3.5-9B`, packing + fla; env knobs `BATCH_SIZE/GRAD_ACCUM/GRAD_CKPT/PACK/DL_WORKERS/EPOCHS/LR`):
  - `spark/train_sft.py` — TRL `SFTTrainer`, prose buckets (LoRA r=32). The simple path.
  - `spark/train_sft_agentic.py` — adds native tool-call **trajectories** (Nemotron-Agentic-v1 `{messages,tools}`); manual tokenize + Trainer (the agentic format can't be a TRL/Arrow column). Teaches tool-call *emission*.
  - `spark/train_molf.py` + `spark/molf.py` — **MoLF-E** (arXiv:2605.07111, experimental): frozen base + 2 LoRA experts (r=64/128) routed by a custom Sparse-AdamW (EPD Top-1 per module); folds to a standard LoRA adapter on export. Under evaluation vs plain LoRA for knowledge capacity.
- **Perf:** packing (concat short rows → dense `max_length`, no padding waste) + fla cut a 2-epoch 9B run ~83 min → ~24 min at ~72 GB. Naive batch-up *without* packing regresses (padding). Wall-clock is token-bound — bigger batch raises memory + cuts steps, not total time.
- **Serving:** `spark/serve_chat.sh start` — transformers-backed OpenAI server on `:8765` (vLLM v0.21 can't load `qwen3_5`); local REPL `chat_lamark.py` connects unchanged. `serve_vllm.sh` is the NemotronH-era path only.
- **Adapters** (`~/.lamark/checkpoints/`): `qwen3_5-9b-instruct-tools-lora` (identity+knowledge+tools, gate PASS); `qwen3_5-9b-agentic-lora` (also emits native `<tool_call>` — but the raw instruct model already tool-calls, so don't over-prose-train and you keep it); `qwen3_5-9b-molf-lora` (MoLF-E, under eval). **Watch undertraining:** packing collapses to few optimizer steps — raise `EPOCHS` so identity/knowledge actually imprint (a 2-epoch packed run undertrained them).

---

## Load-bearing invariants

These rules are non-negotiable across both the Rust agent and the Python training pipeline.

1. **Never delete or modify the original `LICENSE`.** Nous Research's MIT copyright on the vendored Hermes Agent (`learning/vendor/hermes/`) is mandatory. Add new copyright lines above, never replace.
2. **Never call the product anything other than Lamark** in user-facing strings. The upstream "Hermes" name appears only in `LICENSE`, attribution sections, `learning/vendor/hermes/`, and required MIT notices.
3. **Never bake real user data into git.** Adapters, memory files, training archive, trace bundles, and `.env` are gitignored. Verify with `git status` before commit.
4. **Never use bitsandbytes QLoRA for MoE training on DGX Spark.** Confirmed OOM-at-load at 4% (Kreuzhofer, NVIDIA forum). Use bf16 LoRA only.
5. **Never enable DeepSpeed ZeRO-3 for LoRA training on Qwen3.6 MoE.** Breaks gradients. Use single-device or ZeRO-2.
6. **Never unfreeze the MoE router** during LoRA training. Pre-trained routing is load-bearing.
7. **No non-English strings in user-facing code or docs** outside `learning/vendor/hermes/` (third-party MIT).
8. **`learning/vendor/hermes/` is third-party MIT code.** Only modify files marked `LAMARK-PATCH` and record the diff in `learning/vendor/hermes/MODIFICATIONS.md`. Never rewrite upstream code in place without a patch marker.
9. **Never use greedy decoding with any Qwen3 model.** They loop at greedy. Use `temperature=0.7, top_p=0.8, top_k=20` for tool loops (thinking off) and `temperature=0.6, top_p=0.95, top_k=20` for reasoning (thinking on). Applies to serving, batch eval, and synthetic data generation.
10. **Never refresh the General Anchor more than quarterly.** It is ROM — build once, never rotate nightly or weekly. Once it churns, the forgetting probe loses its fixed reference and catastrophic forgetting becomes undetectable.
11. **Always start SFT from the instruct checkpoint, never the base model.** Chat template, `<tool_call>` grammar, and system-prompt handling are baked into the instruct checkpoint. Starting from the base model requires an alignment corpus we don't have and produces subtly wrong tool-call behavior even at low training loss.
12. **`assistant_only_loss=True` is mandatory for every SFT and DPO run.** Without it the model trains on user and system tokens, learns to predict them, and produces confused multi-turn behavior at inference time despite low training loss. Use Unsloth's `train_on_responses_only` or TRL's `SFTConfig(assistant_only_loss=True)`.
13. **Never tune `lm_head` or `embed_tokens` during SFT.** Those layers are only enabled for Tier 0 CPT (continued pre-training on a BASE model with > 50 MB raw unseen domain text). Enabling them during SFT on an instruct model produces vocabulary drift and unpredictable tokenization changes.
14. **GRPO/RL is a last resort, not a first tool.**
15. **Verify Unsloth support before training any Qwen3.5 dense model.**
16. **Classify trace failures before routing to SFT.** Only REASONING failures (≈10%) belong in the nightly SFT blend. ACTION_REALIZATION + CONTRACT_MISMATCH + TRAJECTORY_DEGENERATION failures (≈90%) belong in the harness evolution pipeline (`harness_evolve.py`). Routing interface failures to SFT wastes training budget and trains the wrong signal.
17. **Never use Action Realization as a security gate.**
20. **MEMIT / EasyEdit knowledge-editing does not work on hybrid architectures** (NemotronH Mamba/MoE, Qwen3.5 linear-attention). EasyEdit's `generate_fast` needs a standard `past_key_values` KV cache these archs don't expose, and `qwen3_5` further requires transformers 5.x while EasyEdit pins ~4.57. Inject knowledge **and** identity via **SFT data**, not weight edits. (Historical: ADR-0010/0011 reference an abandoned L1–L4 memory-layer model — superseded by this SFT approach.)
21. **`qwen3_5` requires transformers 5.x** (absent from 4.57.1). It loads as `Qwen3_5ForCausalLM` (text-only, no vision tower) under `AutoModelForCausalLM`. `Qwen/Qwen3.5-9B-Base` is base; `Qwen/Qwen3.5-9B` is the instruct checkpoint — train/serve the instruct one (invariant 11).
22. **Load Qwen3.5 for inference with `device_map={"":0}`, never `"auto"`.** Under memory pressure `"auto"` CPU-offloads the linear-attention conv layers, and `causal_conv1d` requires CUDA tensors → `RuntimeError: Expected x.is_cuda()`. Ensure the GPU is free (no stray `docker run --rm` containers orphaned by a broken pipe) before serving/probing.
23. **Identity and tool knowledge are data, not config.** Both must be present and oversampled in the SFT blend (identity Q&A banks in `build_dataset.py`; tool facts/QA from `tools.yaml` via `generate_tool_dataset.py`) — a base/instruct model will otherwise confabulate. Serve and probe with `enable_thinking=False` for clean, untruncated answers.
18. **Skills require unit tests before registration.** The `skill_create` tool (MUSE approach) must run all `tests/` pytest files in a sandbox before registering a new skill. An untested skill silently fails at runtime without producing useful feedback. No tests → no registration.
19. **`.memory.md` is per-agent, not per-skill-definition.** When transferring a skill to another agent or deployment, ship only SKILL.md + scripts/ + tests/ — never `.memory.md`. The memory contains agent-specific failure patterns that may mislead a different agent in a different environment. `lamark-harness` realization rules are correctness guardrails evolved from domain traces — they block technically wrong calls silently. `lamark-policy` is the security layer. Mixing them produces either security gaps or excessive agent interruptions. The Gated DeltaNet layers use different projection names than standard Transformer attention. Passing wrong `target_modules` silently trains only the attention layers and ignores SSM layers — check `model.named_modules()` against the LoRA config before starting a run. Most capability gains come from better SFT data, not a more powerful training algorithm. The GRPO tier (v0.2+) requires ≥ 30 consecutive stable nightly SFT nights, implemented task verifiers, and a stable forgetting-probe baseline before it can be activated without reward hacking.

---

## When in doubt

Ask the user. Lamark is single-user and personal — a mistake can corrupt user data with no rollback besides manual backups. Measure twice, cut once.
