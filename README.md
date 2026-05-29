# Lamark

> A locally-hosted AI agent that improves through use — skills, behaviour,
> and model weights all evolve from your interactions, overnight, on your
> own hardware.

Named after **Jean-Baptiste Lamarck**: traits the agent picks up during
conversations become part of the next generation of its weights — rejected
in biology, but exactly what happens here.

**Status: alpha — verified on DGX Spark.** Consumer-GPU (4090/3090/3060)
and Apple Silicon paths are wired but not yet validated on external hardware.

---

## What it does

Lamark runs a five-layer adaptation stack that improves itself without
requiring you to do anything:

| Layer | What changes | Mechanism | Cadence |
|---|---|---|---|
| **Harness** | Interface rules (tool policies, arg validation, loop detection) | LIFE-HARNESS evolution from traces | Weekly |
| **Skills** | Reusable procedures (SKILL.md files) | MUSE creation + SkillOpt optimization | On-demand + weekly |
| **Identity** | "I am Lamark" | `chat_template.jinja` | Rarely |
| **Memory** | Session facts, user preferences | Knowledge-base + RAG | Every turn |
| **Weights** | Deep reasoning capability | LoRA SFT (residual failures only) | Nightly |

The key insight: **90% of agent failures are interface failures, not reasoning
failures** ([LIFE-HARNESS, arXiv:2605.22166](https://arxiv.org/abs/2605.22166)).
The harness and skill layers fix those 90% at zero weight cost. SFT handles the
remaining 10%. This means the model gets lighter training with less forgetting risk.

---

## Hardware tiers

| Tier | Hardware | Training model | Serving |
|---|---|---|---|
| **S** | DGX Spark (128 GB unified) | Qwen3.5-9B / Qwen3.6-35B-A3B | NVFP4 + vLLM |
| **M** | RTX 4090/5090 (24-32 GB) | Qwen3.5-9B QLoRA | llama-server GGUF |
| **L** | RTX 3060/3090 (12-24 GB) | Qwen3.5-4B QLoRA | llama-server GGUF |
| **XS** | Apple M3 Pro (36 GB unified) | Gemma4-27B (MLX) | MLX-LM |

`lamark setup` auto-detects your tier. Switch any time:
```bash
lamark switch-base <model-name>
```

Current active training model: **Qwen3.5-9B-Base** (Qwen3.5 family — hybrid
Gated DeltaNet + Attention, 262K context, Apache 2.0).

---

## Quick install

```bash
curl -fsSL https://raw.githubusercontent.com/Merocle/lamark-agent/main/install.sh | bash

lamark setup        # interactive wizard
lamark chat         # start a session
```

> **v0.1.0-alpha.0 is in private testing.** Invited testers: see
> [`docs/private-testing.md`](docs/private-testing.md) for install paths.

---

## How it learns

```
You chat with Lamark
        │
        ▼
HarnessStack wraps every turn:
  Contract (tool policies) → Skill (retrieved SKILL.md) → LLM →
  Action Realization (validate before exec) → Trajectory Regulation (detect loops)
        │
        ▼
Trace bundle written to ~/.lamark/traces/<rollout_id>/
        │
        ├── Failures classified:
        │     90% interface → weekly harness/skill evolution (no weight change)
        │     10% reasoning → nightly SFT training blend
        │
        ▼
Nightly (T+0..T+9h):
  collect traces + git commits → redact → paraphrase (×4 synthetic variants)
  → transform → quality filter → 70/20/10/5 blend
  → SFT LoRA (Qwen3.5-9B, ~5h) → eval gate → promote adapter
        │
        ▼
Weekly (Saturday):
  harness_evolve (fixes contract/realization/regulation failures)
  SkillOpt Curator (optimizes SKILL.md files, +23 pp avg documented)
  MUSE Manage (merges/prunes skill library)
        │
        ▼
Monthly (day 30):
  merge LoRA delta → base weights → requantize → new lamark-base-vYYYY.MM
```

Detailed flow: [`docs/flow.md`](docs/flow.md)

---

## Commands

```bash
lamark setup                        # first-time wizard
lamark chat                         # interactive REPL
lamark serve start|stop|status      # local model server
lamark status                       # health overview
lamark switch-base <model>          # change base model
lamark train --now [--force]        # trigger training immediately
lamark train --status               # last / next / pending
lamark config get|set|show          # edit config
lamark logs vllm|nightly|skills     # tail logs
lamark skill list|view|create       # manage skill library
lamark skill curator --run          # run SkillOpt Curator now
```

---

## Configuration

`~/.lamark/config.yaml`:

```yaml
model:
  provider: vllm                    # vllm | ollama | llamacpp | anthropic | openai
  base_url: http://localhost:8000/v1
  name: Qwen/Qwen3.5-9B-Base
  context_length: 32768

training:
  frequency: daily                  # daily | weekly | manual
  min_pairs: 50                     # skip if fewer new pairs
  teacher_model: gpt-5.4-mini       # configurable teacher for synthetic data

skills:
  curator_interval_hours: 168       # weekly SkillOpt run

sandbox:
  default: local                    # local | docker | ssh | kubernetes
```

---

## Architecture

Three independent processes, no shared database:

```
Agent Runtime (Rust)  ◄──HTTP──►  Knowledge Base (Kotlin)
        │                                   ▲
        │ trace files                       │ HTTP
        ▼                                   │
Training Pipeline (Python)  ──────────────►┘
```

- **Agent runtime** (`agent/`) — Rust, interactive, one process per session
- **Knowledge base** (`../knowledge-base`) — Kotlin/Spring, long-running, system of record
- **Training pipeline** (`learning/`) — Python, cron-scheduled, GPU box

Full specification: [`SPEC.md`](SPEC.md)  
Layer plans: [`docs/plan/`](docs/plan/)  
System flows: [`docs/flow.md`](docs/flow.md)

---

## Privacy

Everything stays local by default:
- All conversations stay on your hardware
- Training archive (`~/.lamark/archive/`) is on your filesystem under your permissions
- Secrets are stripped before any data reaches the training pipeline
  (Gitleaks + TruffleHog → Presidio + GLiNER)
- Cloud fallback is opt-in and strips PII before transmission

---

## Roadmap

**Phase 1 (alpha, current):**
- CLI + installer
- L1 identity via chat_template, L2 memory via knowledge-base
- SFT LoRA nightly pipeline (Qwen3.5-9B on DGX Spark)
- HarnessStack scaffolding (Contract / Skill / Realization / Regulation)
- MUSE skill creation + SkillOpt Curator (weekly)
- InferredBugs × paraphrase dataset pipeline (gpt-5.4-mini)

**Phase 2 (beta):**
- Consumer-GPU validation (4090 / 3090 / 3060)
- Encryption-at-rest for archive + adapters
- `lamark eval` for benchmark-grade adapter promotion
- GRPO/RLVR after SFT baseline is stable (NeMo RL / Harbor + SkyRL)
- Resumable model downloads

**Phase 3 (release):**
- Apple Silicon / AMD paths
- Migration story for model family upgrades
- Documentation site

---

## License & attribution

MIT. Lamark vendors **Hermes Agent** by [Nous Research](https://nousresearch.com)
(MIT) at SHA `874c2b1f` with `LAMARK-PATCH A.2/A.3/A.4/A.6` applied.
Full attribution in `LICENSE` and `learning/vendor/hermes/MODIFICATIONS.md`.
