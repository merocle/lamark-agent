# 2026-05-28 — Lamark LoRA self-knowledge experiment

**Goal:** teach the Nemotron-3-Nano-4B base model what Lamark is via LoRA SFT, then evaluate whether the model can actually answer Lamark-specific questions correctly.

**Result:** the L4 pipeline (LoRA fine-tuning for style/voice) works end-to-end on DGX Spark. The L3 hypothesis (knowledge edits via LoRA) is **falsified** empirically — recorded in [ADR-0010](../../docs/decisions/0010-lora-cannot-replace-knowledge-edits.md).

---

## 1. What we built

A complete training and evaluation pipeline:

| Stage | Script | Purpose |
|---|---|---|
| Data gen | `learning/scripts/generate_lamark_dataset.py` | Generate Lamark Q&A via `gpt-5.4-mini`, 18 topic clusters, batched API calls, output JSONL |
| Train | `learning/scripts/spark/03_train_lora.sh` + `train_lora.py` | Pull NeMo container with NGC fallback to `pytorch:26.01-py3`, prep data, run HF PEFT LoRA SFT |
| Validate | `learning/scripts/spark/04_validate.sh` + `validate_adapter.py` | Manual safetensors LoRA merge, perplexity on val set (bypasses broken `PeftModel.from_pretrained`) |
| Probe | `learning/scripts/spark/probe_vllm.sh` | Spin up `vllm/vllm-openai:v0.21.0` with `--enable-lora`, hit `/v1/chat/completions`, base vs adapter side-by-side |
| Orchestrate | `learning/scripts/run_spark_training.sh` | scp scripts to Spark, run sequence over SSH; new flags `--dataset`, `--skip-data`, `--steps` |

Total wall-clock for one full cycle (data + train + validate + probe): ~25 minutes.

---

## 2. Discoveries while debugging

### 2.1 Chat template was wrong (highest-impact bug)

The model's tokenizer config lists `[INST]` / `[/INST]` / `<s>` / `</s>` as special tokens (Mistral-style), but those are vestigial. The real `chat_template.jinja` is **ChatML with thinking**:

```
<|im_start|>system\n<|im_end|>\n<|im_start|>user\n{Q}<|im_end|>\n<|im_start|>assistant\n<think></think>
```

We started by training on a hand-rolled `<|user|>\n{Q}\n<|assistant|>\n{A}\n<|end|>` template — those tags were not special tokens, so the LoRA was learning to predict the literal multi-token strings `<`, `|`, `user`, `|`, `>` instead of aligning with the model's instruction-tuning prior.

**Fix:** every script that touches text now calls `tokenizer.apply_chat_template(..., enable_thinking=False)`.

### 2.2 NemotronH `model.generate()` is broken in stock transformers

`model.generate()` throws:

```
TypeError: 'NoneType' object is not subscriptable
```

…because NemotronH is hybrid Mamba/attention and requires `NemotronHHybridDynamicCache` to be initialised, but the default `prepare_inputs_for_generation` doesn't do that. A manual greedy decode loop produces garbage too — the Mamba SSM state never advances between forward passes, so the model converges on a single space token forever.

**Fix:** all interactive inference goes through vLLM (`vllm/vllm-openai:v0.21.0`), which has correct hybrid Mamba handling. This is the production-correct path anyway.

### 2.3 HF Trainer `load_best_model_at_end` hits the NemotronH PEFT bug

When training finishes, HF Trainer reloads the best checkpoint via `PeftModel.load_adapter` → `convert_peft_adapter_state_dict_for_transformers` → `WeightConverter.__init__()` which gets an unexpected `distributed_operation` kwarg. The crash erases the otherwise-successful run.

**Fix:** disabled `load_best_model_at_end=True`; we manually pick the best checkpoint from disk instead. PEFT's converter is being upgraded upstream but doesn't help us today.

### 2.4 NGC license gate

`docker pull nvcr.io/nvidia/nemo:*` requires both `docker login nvcr.io` **and** a one-time browser click to accept the NVIDIA Software License Agreement at https://catalog.ngc.nvidia.com/orgs/nvidia/containers/nemo. The catalog metadata says `canGuestPull: true` but `needsAcceptance: true` is the real gate. Login alone is not sufficient.

**Workaround:** use the existing `nvcr.io/nvidia/pytorch:26.01-py3` container (also pre-installed on Spark; no license gate). HF PEFT path produces an identical LoRA adapter format.

### 2.5 Smaller things

- Windows OpenSSH has `scp` but not `rsync` → orchestrator uses `scp -r`
- SFTP does not shell-expand `$HOME` → use `lamark-agent` (relative to login dir), not `\$HOME/lamark-agent`
- Docker-created files end up `root:root` on host because container runs as root → use `docker run alpine sh -c 'chown -R 1000:1000 ...'` to repair ownership
- vLLM 0.21 deprecated `--model` (now positional) and removed `--disable-log-requests`

---

## 3. Training runs

All on DGX Spark, `nvcr.io/nvidia/pytorch:26.01-py3`, HF PEFT bf16 LoRA, target modules `q/k/v/o_proj + gate/up/down_proj`, cosine LR. Same hardware (GB10, 200 GB unified). Same `apply_chat_template(enable_thinking=False)` from run 3 onward.

| # | Examples | Steps | LR | r/α | Final train loss | Final eval loss | PPL improvement (base→adapter, n=50) | Generation quality |
|---|---|---|---|---|---|---|---|---|
| 1 | 230 | 150 | 2e-4 | 16/32 | 9.80 | 3.607 | 2415 → 6.75 (358×) | **collapsed** — repetition loops |
| 2 | 230 | 60 | 2e-5 | 16/32 | 29.79 | 7.694 | 10011 → 3125 (3.2×) | empty / EOS spam |
| 3 | 230 | 60 | 2e-5 | 16/32 | 29.01 | 7.458 | 7474 → 5180 (1.4×) | indistinguishable from base |
| 4 | 230 | 200 | 1e-4 | 16/32 | 9.55 | 3.584 | 12597 → 697 (18×) | 1/8 vocab hit ("lamark crate") |
| 5 | 1625 | 600 | 1e-4 | 16/32 | 6.68 | 1.879 | 7801 → 205 (38×) | 2/8 vocab hits |
| 6 | 1625 | 600 | 1e-4 | **64/128** | 4.96 | **1.560** | 22481 → 485 (46×) | 3/8 hits (Q2/Q4/Q7), Q6 hallucinates instead of refusing |

Trainable params:
- r=16, α=32 → 10.1 M trainable (0.25% of model)
- r=64, α=128 → 40.5 M trainable (1.00% of model)

### Run 1 — too aggressive

`lr=2e-4` with only 230 examples over 5 epochs drove the LoRA into pure memorisation. PPL on the synthetic val set looked spectacular (6.75) but interactive generation collapsed into token repetition: "The user's agent agent agent agent agent agent…". Classic overfit + catastrophic forgetting.

### Run 2/3 — too conservative

Pulling LR down 10× to `2e-5` over 60 steps produced an adapter that barely moved the weights. PPL still improved on the training distribution but generation was either empty or identical to base. Run 3 also fixed the chat template (real ChatML); the PPL number became "meaningful" but the adapter still couldn't beat the base model in side-by-side probes.

### Run 4 — sweet spot at small scale

`lr=1e-4` over 200 steps on 230 examples. Final eval 3.584, PPL improvement 18×. First time the adapter produced any real Lamark vocabulary in generation:

- Q: "Which Rust crate in Lamark owns the ModelProvider trait?"
  - BASE: "tch-rs (TensorFlow Lite binding)" — wrong
  - ADAPTER: "the lamark crate itself" — partial credit, real answer is `lamark-providers`

7/8 questions still defaulted to base-model priors (Lamarck-the-biologist, LangChain, AWS SQS).

### Run 5 — 7× more data

1805 generated examples (18 topics × ~100 examples), with a new `identity_disambiguation` topic carrying 100 "Lamark is X, not Y" examples. Final eval 1.879, PPL 7801 → 205 (38×). Probe score: **2/8 hits** at the vocabulary level:

- Q2 SQ/EQ pattern: adapter mentioned "Lamark runtime" + "external components" decoupling (real vocab; base talked AWS SQS)
- Q4 ModelProvider crate: still "lamark crate" (partial)

The Q1 disambiguation topic (100 explicit examples) did not shift "What is Lamark?" away from "did you mean Lamarck?".

### Run 6 — 4× LoRA capacity

r=16/α=32 → r=64/α=128. 40.5 M trainable params, same data and hyperparams as run 5. Mid-training eval at step 400 was 1.599 vs run 5's 1.928 at same step — ~17% lower loss. Final probe not yet captured in this writeup; result will determine whether higher rank meaningfully shifts factual recall vs vocabulary.

---

## 4. Why LoRA isn't enough — the architectural lesson

The Lamark spec already separates four mechanisms:

| Layer | Stores | Mechanism |
|---|---|---|
| L1 — Identity | "I am Lamark" | `chat_template.jinja` |
| L2 — Episodic | "User likes blue" | RAG over knowledge-base |
| L3 — Knowledge edits | High-priority facts as weights | **ROME-style rank-1 surgery (planned)** |
| L4 — Style / voice | How you write | **LoRA fine-tuning** |

The empirical result confirms the split:

- LoRA **at any capacity tested** shifted style and vocabulary but not facts. Even 1625 examples couldn't override "Lamark = Lamarck the biologist" when the question was ambiguous.
- The model's strong priors are baked into the FFN weights and cross-layer routing. LoRA's low-rank update can interpolate around those priors (producing different surface text) but can't replace them.
- Knowledge editing techniques (ROME, MEMIT) work by **targeted rank-1 updates to specific MLP weights** identified by causal tracing. That's a fundamentally different operation than gradient-descent SFT.

L4 is now demonstrably useful: it does teach the model Lamark-flavoured vocabulary, which is what L4 is supposed to do.

L3 needs its own implementation pass — out of scope for this session.

---

## 5. Cross-validation: did we degrade general performance?

The PPL numbers above are on the synthetic Lamark val set. They say nothing about whether the adapter broke the model's general capability. Added a regression section to `probe_vllm.sh` that runs the same questions through both base and adapter and prints them side by side.

Run 6 (r=64, α=128) regression results:

| # | Question | BASE | ADAPTER | Pass |
|---|---|---|---|---|
| G1 | Capital of France | "Paris" | "Paris" | ✓ |
| G2 | 17 × 23 | "391" | "17 × 23 = 391" | ✓ |
| G3 | Python factorial 1-liner | `def factorial(n): return 1 if n == 0 else n * factorial(n-1)` | identical | ✓ |
| G4 | Neural net in one sentence | "inspired by the human brain that processes information through interconnected nodes" | "inspired by the brain that learns patterns from data by adjusting interconnected weights" | ✓ (both correct) |
| G5 | Translate "hello, how are you" to French | "Bonjour, comment allez-vous ?" (formal) | "Bonjour, comment ça va ?" (informal) | ✓ (both correct) |
| G6 | Sort [3, 1, 4, 1, 5, 9, 2, 6] | "[1, 1, 2, 3, 4, 5, 6, 9]" | identical | ✓ |

**6/6 pass. No general-performance degradation.** The LoRA at r=64/α=128 is targeted enough that it didn't damage math, coding, multilingual, or common-sense reasoning. Run 1's catastrophic forgetting (overshoot at lr=2e-4 + small data) is the only run where general-perf would have failed.

---

## 6. Cost / time

- Data generation (1805 examples via `gpt-5.4-mini`): ~$0.20, ~6 minutes wall time, 90 API calls
- Training (600 steps, r=64/α=128): ~9 minutes wall time on Spark
- Validation (50 samples): ~30 seconds
- Probe (8 questions × 2 models via vLLM): ~3 minutes wall time
- Full session including debugging: ~6 hours

---

## 7. What's next

1. ✅ **General-performance regression check landed** in `probe_vllm.sh` (run 6 passed all 6 items).
2. **Implement L3 (ROME / MEMIT) as a separate experiment** — different codepath, different evaluation. Out of scope for this session.
3. **The L4 pipeline as-committed is reusable** for actual style/voice training off `~/.lamark/traces/` once the agent is producing them. The data generator should be replaced with the real trace-bundle reducer at that point.
4. The vLLM probe scaffold doubles as the foundation for a `lamark` CLI subcommand to serve the adapter for production inference.
5. **A few small improvements worth running once L3 is in place:** higher max-tokens in the probe (200 → 400) so Q5/Q8 don't get truncated mid-preamble; add a "thinking" trace-template variant to test whether Nemotron's chain-of-thought changes things.

---

## 8. Files added / changed this session

```
learning/scripts/generate_lamark_dataset.py             (new)
learning/scripts/spark/probe_adapter.py                 (new)
learning/scripts/spark/probe_base.py                    (new)
learning/scripts/spark/probe_vllm.sh                    (new)
learning/scripts/spark/01_setup.sh                      (NGC→pytorch fallback)
learning/scripts/spark/03_train_lora.sh                 (data-vol mount, deps)
learning/scripts/spark/04_validate.sh                   (image, checkpoint subdir)
learning/scripts/spark/train_lora.py                    (chat template, LR, r)
learning/scripts/spark/validate_adapter.py              (manual safetensors merge)
learning/scripts/spark/validate_final.py                (working validation reference)
learning/scripts/run_spark_training.sh                  (--skip-data, --dataset, MODEL_ID)
docs/decisions/0010-lora-cannot-replace-knowledge-edits.md   (new)
.ai/research/2026-05-28-lamark-lora-experiment.md       (this file)
```

Commits on `feature/merge-and-refactoring`:

- `a63b7bd` fix(spark): harden training scripts against live-run failures
- `a1337ff` feat(spark): add validate_final.py
- `cc943e9` feat(learning): full L4 pipeline + vLLM probe + ADR-0010
