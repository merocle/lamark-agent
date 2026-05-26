# Lamark

> A locally-hosted personal AI agent that learns about you over time and
> bakes accumulated context into its own model weights — overnight, on
> your own hardware.

**Status: alpha.** Verified on two independent DGX Spark hosts. Not yet tested
on consumer NVIDIA GPUs (4090/5090/3090) — coming in Phase 2.

Named after **Jean-Baptiste Lamarck**, whose theory of inheritance of
acquired characteristics is rejected in biology but precisely describes
what this agent does: traits it picks up during conversations with you
become part of the next generation of its weights.

---

## Quick install

```bash
curl -fsSL https://raw.githubusercontent.com/Merocle/lamark-agent/main/install.sh | bash

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

## Hardware tiers

| Tier | Hardware | Default model | Status |
|---|---|---|---|
| **S** | DGX Spark, A100 80GB, H100 | Qwen3.6-35B-A3B MoE (67 GB) | **verified** |
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
lamark train --now [--force]   # Run retrain immediately
lamark train --status       # When was last / next scheduled / pending pairs
lamark train --config       # Show training frequency + threshold
lamark config get|set|show  # Edit Hermes-home config
lamark logs vllm|nightly|download|chat
```

---

## Configuration

User config lives at `~/.lamark/hermes-home/config.yaml`. Common knobs:

```yaml
model:
  default: qwen-3.6-35b-a3b-moe   # which model to serve (registry name)
  provider: lm-studio              # transport for the OpenAI-compat /v1 surface
  base_url: http://127.0.0.1:8000/v1
  context_length: 65536            # override the model's reported context (Hermes minimum: 64K)

training:
  frequency: daily                 # daily / weekly / manual
  min_pairs: 50                    # don't retrain unless this many new pairs accumulated
```

Edit live via the CLI:

```bash
lamark config set training.frequency weekly
lamark config set training.min_pairs 100
lamark config set model.default qwen-3.6-14b-dense
```

---

## How learning works

```
You chat with Lamark
       ↓
Hermes memory_tool writes facts to USER.md
       ↓
LAMARK-PATCH A.4 mirrors each write into ~/.lamark/archive/
       ↓
Every night (or weekly) systemd timer fires
       ↓
lamark-nightly-train.sh checks: do we have >= min_pairs new content?
       ↓ yes                       ↓ no
Train a fresh LoRA adapter         Skip, log "below threshold"
       ↓
Eval gate (identity probe + safety probe + coherence probe)
       ↓ pass                      ↓ fail
Promote new adapter to default     Keep previous, log failure
```

You can force a retrain regardless of threshold with
`lamark train --now --force`. The eval gate is intentionally a smoke check,
not a benchmark — for full evaluation we plan a separate `lamark eval`
command in Phase 2.

---

## Operational notes

- **Stop `lamark serve` before `apt upgrade`.** A combination of vLLM
  serving + kernel/driver swap + post-reboot model reload can push the
  host into sustained memory pressure (we saw it on Spark-01 — the host
  remained up but SSH/Tailscale stopped responding for ~20 minutes
  before vLLM finally crashed and freed memory). `lamark serve stop`
  first, then upgrade, then `lamark serve start`.
- **Default `max_model_len` is 32768** for tier S. The Hermes Agent
  minimum of 64K is satisfied via an in-memory accounting override in
  the config — not by allocating an actual 65K KV cache, which would
  consume ~10 GB extra unified memory on Spark and increase the risk
  of the above pressure event.
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
- ✅ Hybrid retrain trigger (frequency × min_pairs threshold)
- ✅ L1 identity via chat_template, verified portable across vLLM versions
- ✅ L2 cross-session memory via Hermes
- ✅ Upstream `vllm/vllm-openai:v0.21.0` for serving (no custom image needed)

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
(MIT) at SHA `874c2b1f` with our `LAMARK-PATCH A.2/A.3/A.4/A.6` series applied.
Full attribution preserved in `LICENSE` and `vendor/hermes/UPSTREAM.md`.
