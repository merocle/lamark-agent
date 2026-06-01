# Lamark

> **Your AI. On your hardware. Getting smarter about you every night.**
>
> A personal AI agent that runs entirely on machines you own, learns from
> every conversation, and bakes what it learns straight into its own model
> weights — overnight, with no data ever leaving the box unless you say so.

**Status: alpha — actively developed.** See [hardware tiers](#hardware-tiers)
below for what it runs on.

---

## Why Lamark

**Local-first, not local-only-in-the-marketing-sense.** Lamark lives on
your hardware — a box in your home, your account, your disk. There are no
cloud accounts to sign into, no shared inference endpoint, no telemetry.
Your conversations, your memory, and your training data are plain files
under `~/.lamark/` that never leave the machine by default. A redaction
gate refuses — unconditionally — to write verified secrets (API keys,
tokens, JWTs) anywhere, even to your own disk.

**It actually learns — in its weights, not just a vector store.** Named
after **Jean-Baptiste Lamarck**, whose idea that acquired traits are
inherited is wrong for biology but exactly right here: every night Lamark
curates the day's conversations and trains a fresh LoRA adapter on them.
What you teach it today becomes part of how it thinks tomorrow — a real
parameter update, gated by an automatic quality check, with a notification
when a new version of *your* model goes live. Every night it wakes up a
little more yours.

**Cloud is a tool it reaches for, not a place it lives.** When a question
genuinely needs more than the local model — deep reasoning, current facts —
Lamark can escalate: web-search grounding for facts, or a stronger cloud
model for hard reasoning. But it asks first, redacts what it sends, and
logs every call. The default is, and stays, local.

---

## Quick install

```bash
curl -fsSL https://raw.githubusercontent.com/merocle/lamark-agent/main/install.sh | bash

# After install:
lamark setup        # interactive wizard (3 branches: Local / Existing endpoint / Cloud-first)
lamark chat         # open the interactive REPL
```

The installer takes ~3-5 minutes (mostly Python deps). It does **not**
download a model — that decision is deferred to `lamark setup`, where
you choose between running the model on your hardware, connecting to an
endpoint you already operate, or starting with a cloud provider.

---

## What it does

Lamark separates four kinds of "knowing" — each with its own mechanism,
because shoehorning all of them into LoRA fine-tuning doesn't work at
realistic scale.

| Layer | Stores | Mechanism | Update cadence |
|---|---|---|---|
| **L1 — Identity** | "I am Lamark" | `chat_template.jinja` (config) | Rarely |
| **L2 — Episodic memory** | "User likes blue" | Hermes memory tool + RAG retrieval | Every turn |
| **L3 — Knowledge edits** | High-priority facts as weights | ROME-style rank-1 surgery *(planned)* | Nightly |
| **L4 — Style / voice** | How you write | LoRA fine-tuning on user turns | After ~1000 turns |

This is what makes the "Lamarckian" claim honest: L3 and L4 modify the
model's actual parameters, not a retrieval index. L1+L2 already deliver
the user-visible "Lamark knows me" experience today; L3 and L4 are the
slow, real learning that compounds.

---

## Smart routing & cloud escalation

A 35B local model confabulates instead of admitting ignorance, so Lamark
routes on the **question's intent** (a robust classification task) rather
than the model's self-confidence. Every incoming message is triaged
(cheap regex → local-LLM classifier) before the agent answers:

- **Factual** ("when was X born", "what is Y") → a `web_search` is run and
  injected as grounding; the model is instructed to answer **only from the
  results**, never from its weights. This is the anti-confabulation mechanism
  (a strong prompt-level bias, enforced by the model's compliance — not a
  hard decode constraint).
- **Hard reasoning** (research-level proofs/derivations, deep expertise)
  → the model is directed to call `ask_cloud`, which delegates to a stronger
  cloud model (Claude / GPT / Gemini) through your LiteLLM proxy. You
  approve each call in Telegram before anything leaves the box, PII is
  redacted, and every call is logged to `~/.lamark/cloud_calls.jsonl`.
- **Personal / casual / code** → answered locally.

The privacy default is local; the cloud is reached only on an explicit
request or a genuinely-hard question, and only after your ✅. You can also
switch models manually in Telegram with `/model` (local `lamark`, base
`qwen-base`, or any curated cloud model).

---

## Hardware tiers

| Tier | Hardware | Default model | Status |
|---|---|---|---|
| **S** | DGX Spark, A100 80GB, H100 | Qwen3.6-35B-A3B MoE, FP8 (~35 GB) | **verified** |
| **M** | RTX 4090 24GB, 5090 32GB, A100 40GB | Qwen3.6-14B dense (28 GB) | experimental |
| **L** | RTX 3090, 4080, A4000 | Qwen3.6-7B dense (15 GB) | experimental |
| **XS** | RTX 4060, Mac M-series | Qwen2.5-1.5B (3 GB, mostly for tests) | experimental |

**v0.1 alpha is verified only on tier S (DGX Spark).** Tiers M/L/XS are
wired into the registry and the installer, but the Spark-specific quirks
(`flash_attn` purge, `eager_loader_patch`, `TORCH_CUDA_ARCH_LIST=12.1`)
may not transfer cleanly to other CUDA architectures. If you try one of
the experimental tiers, `lamark setup` will warn and ask for confirmation.
Phase 2 will validate consumer GPUs on real hardware and fix what breaks.

`lamark setup` auto-detects your tier and picks an appropriate model.
Switch any time with `lamark switch-base <name>`.

---

## Commands

```bash
lamark setup                # First-time wizard
lamark chat                 # Interactive REPL
lamark serve start|stop|restart|status
                            # Local vLLM model server
lamark status               # Health overview
lamark switch-base <model>  # Change default base
lamark train --now [--force]      # Run retrain immediately
lamark train --status             # Last/next/pending pairs
lamark train --config             # Show training frequency + threshold
lamark train --schedule           # Show current systemd timer schedule
lamark train --schedule "<spec>"  # Set timer to fire on this OnCalendar spec
                                  #   "*-*-* 03:00:00"      nightly 3am
                                  #   "Sun *-*-* 04:30:00"  weekly Sun 04:30
lamark train --schedule off       # Disable the timer (preserves unit files)
lamark gateway install            # Install the messaging gateway as a
                                  # systemd (Linux) / launchd (macOS) service
lamark config get|set|show  # Edit Hermes-home config
lamark logs vllm|nightly|download|chat
```

---

## Configuration

User config lives at `~/.lamark/hermes-home/config.yaml`. Common knobs:

```yaml
model:
  default: lamark                  # served alias: FP8 base + current LoRA adapter
  provider: custom                 # reads base_url directly (no env-var dance)
  base_url: http://127.0.0.1:8000/v1
  context_length: 131072           # 128K

training:
  min_pairs: 50                    # don't retrain unless this many new pairs accumulated

triage:
  enabled: true                    # question-intent routing (web grounding / cloud escalation)
  model: lamark                    # local model used for the cheap classification pass

custom_providers:
  - name: lamark-local             # the local vLLM (qwen-base + lamark adapter)
    base_url: http://127.0.0.1:8000/v1
providers:
  litellm-cloud:                   # curated cloud models for ask_cloud + /model
    base_url: https://your-litellm-proxy/v1
    key_env: LITELLM_API_KEY
    discover_models: false
    models: [openai/gpt-5.5, anthropic/claude-opus-4-5, ...]
```

Secrets (`TELEGRAM_BOT_TOKEN`, `LITELLM_API_KEY`, `TAVILY_API_KEY`) live in
`~/.lamark/hermes-home/.env`, never in `config.yaml`.

Edit live via the CLI:

```bash
lamark config set training.frequency weekly
lamark config set training.min_pairs 100
lamark config set model.default qwen-3.6-14b-dense
```

---

## How learning works

```
You chat with Lamark via Telegram
       ↓
LAMARK-PATCH A.9 hook fires `on_processing_complete`
       ↓
src/lamark/capture/pair_writer.py appends one ChatML record
to ~/.lamark/archive/incoming/<date>.jsonl  (source=user_explicit,
confidence=0.5 — implicit positive signal)
       ↓
systemd --user timer `lamark-trainer.timer` fires on its schedule
(configured via `lamark train --schedule "..."`, default nightly 3am)
       ↓
Curation: regex pre-kill of debug noise → local-LLM judge keeps only
good-behaviour pairs (cached) → synthetic bootstrap data decays as real
conversations accumulate. (You can also trigger a run from Telegram —
"retrain now" → the `train_now` tool shows a downtime-warning card.)
       ↓
lamark-nightly-train.sh checks: do we have >= min_pairs new content?
       ↓ yes                       ↓ no
Train a fresh LoRA adapter         Skip silently (no notification)
       ↓
Eval gate (identity probe + safety probe + coherence probe)
       ↓ pass                      ↓ fail
Promote new adapter to default     Keep previous adapter
       ↓                            ↓
🎓 Telegram notification           ⚠️ Telegram notification
"adapter promoted"                  "adapter rejected, base unchanged"
```

Every terminal state of a run lands as a structured line in
`~/.lamark/train-history.jsonl`. Force a retrain regardless of
threshold with `lamark train --now --force`. The eval gate is
intentionally a smoke check, not a benchmark — for full evaluation
we plan a separate `lamark eval` command in Phase 2.

---

## Production deployment

For a long-running install (the intended product shape — agent
always-on, Telegram bot reachable, trainer firing on schedule),
everything runs as systemd `--user` services on Linux. The `lamark`
CLI installs and manages them for you:

```bash
# Install the messaging gateway (Telegram + Discord + ... — whichever
# you've configured tokens for in ~/.lamark/hermes-home/.env)
lamark gateway install

# Install the nightly trainer + enable timer (default: every day at 3am)
lamark train --schedule "*-*-* 03:00:00"
```

Both services use systemd lingering so they keep running after you
log out. Verify with `systemctl --user is-active hermes-gateway`
and `systemctl --user list-timers`.

The model server (vLLM) runs in a Docker container managed by
`lamark serve` — independent of systemd, so it stays untouched
when you reload the gateway or training services.

On macOS, the equivalent paths use launchd, but only the *gateway*
side is verified there. macOS is recommended for development;
production lives on the box with the GPU.

## Operational notes

- **Stop `lamark serve` before `apt upgrade`.** A combination of vLLM
  serving + kernel/driver swap + post-reboot model reload can push the
  host into sustained memory pressure (we saw it on Spark-01 — the host
  remained up but SSH/Tailscale stopped responding for ~20 minutes
  before vLLM finally crashed and freed memory). `lamark serve stop`
  first, then upgrade, then `lamark serve start`.
- **Default `max_model_len` is 131072 (128K)** for tier S on the FP8
  base — the quantized weights free up enough unified memory to run the
  full window comfortably (with `gpu_memory_utilization: 0.45` and
  `--max-num-seqs 128`). The BF16 entry is retained in the registry as a
  conservative fallback.
- **`lamark logs vllm`** is your friend when something feels slow.
  Repeated `systemd-journald: Under memory pressure, flushing caches.`
  in your kernel log is the canary.

---

## Privacy

The reason Lamark exists on your hardware instead of in the cloud:

- All conversations stay local by default
- The training-data archive (`~/.lamark/archive/`) is a plain JSONL file
  on your filesystem — your shell-level permissions are your security
- A redaction pipeline (LAMARK-PATCH A.3) refuses to persist verified
  secrets (AWS keys, GitHub PATs, OpenAI keys, JWTs) to memory or
  training data — unconditional, no override
- Optional cloud fallback exists for queries the local model can't
  handle, but it engages only with explicit user consent and PII is
  stripped before the request leaves your machine

**Honest limitation for v0.1:** archive is stored plaintext. Phase 2
adds opt-in age-encryption with a passphrase. If you have your own
disk-encryption layer, that's enough for now.

---

## Roadmap

**Phase 1 (alpha, now):**
- ✅ One-command installer (`install.sh`)
- ✅ Model registry + hardware tier dispatch (Spark / 4090 / 3090 / Mac)
- ✅ Unified `lamark` CLI (setup / chat / serve / status / switch-base / config / train / logs)
- ✅ L1 identity via chat_template, verified portable across vLLM versions
- ✅ L2 cross-session memory via Hermes
- ✅ Upstream `vllm/vllm-openai:v0.21.0`, FP8 quant (~48 tok/s avg on Spark, peaks ~53)
- ✅ Auto pair-capture from Telegram + nightly LoRA trainer (systemd timer,
  user-tunable schedule, Telegram notifications on promote/reject)
- ✅ Local-model curation before training (debug-noise drop + synthetic decay)
- ✅ Question-intent triage: factual → mandatory web grounding,
  hard reasoning → cloud escalation
- ✅ `ask_cloud` — privacy-preserving cloud escalation via LiteLLM, with
  per-call Telegram approval + redaction + audit log
- ✅ On-demand retrain from chat (`train_now`, downtime-warning card)

**Phase 2 (beta, ~weeks):**
- Resumable HF model downloads (67 GB can't tolerate a flaky connection)
- Encryption-at-rest opt-in for archive + adapters
- L3 ROME knowledge-editing for high-priority facts (~50 LOC patch to EasyEdit
  for transformers-5.x compat; planned)
- Consumer-GPU verification (run Phase 1 on a 4090 box, fix what breaks)
- `lamark eval` for proper benchmark before adapter promotion

**Phase 3 (release):**
- Hardware abstraction beyond CUDA (Apple Silicon, AMD)
- L4 style LoRA validated on 1000+ accumulated user turns
- Migration story when Qwen releases v3.7 or later
- Telemetry opt-in (anonymous error reports)
- Documentation site

---

## License & attribution

MIT. Lamark vendors **Hermes Agent** by [Nous Research](https://nousresearch.com)
(MIT) at SHA `874c2b1f` with our `LAMARK-PATCH` series applied:

| Patch | What it does |
|---|---|
| A.2 | Rebrand Hermes → Lamark in user-visible strings |
| A.3 | Redaction gate — block secrets before they reach memory/skills |
| A.4 | Archive mirror — memory writes also land in the training archive |
| A.7 | launchd plist env injection (macOS gateway) |
| A.8 | systemd unit env injection (Linux gateway) |
| A.9 | Auto pair-capture hook on Telegram message completion |
| A.10 | `ask_cloud` — privacy-preserving cloud escalation via LiteLLM |
| A.11 | Register `ask_cloud` / `train_now` toolsets so they reach the agent |
| A.12 | Gateway-correct blocking approval (Telegram ✅/❌ card) |
| A.13 | Question-intent triage hook (web grounding / cloud escalation) |
| A.14 | `train_now` — on-demand retrain from chat with a downtime card |

The full, authoritative change log (touched files + rationale per patch,
plus the `src/lamark/` layer and serving/infra deltas) lives in
[`vendor/hermes/MODIFICATIONS.md`](vendor/hermes/MODIFICATIONS.md).
Attribution preserved in `LICENSE` and `vendor/hermes/UPSTREAM.md`.
