# Operational notes — Lamark L4 LoRA training pipeline

Cheat sheet that compresses the lessons from `.ai/research/2026-05-28-lamark-lora-experiment.md`
and `docs/decisions/0010-lora-cannot-replace-knowledge-edits.md` into the things you
need to remember to run the pipeline.

**Audience:** future me, or any AI assistant resuming this work stream.

---

## What the pipeline does

`learning/scripts/` contains a working L4 LoRA SFT pipeline that takes:
- a **base model** from HuggingFace (default: `nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16`)
- a **conversation JSONL** dataset (Q&A pairs, NeMo-style: `{"conversations":[{"role":"user","value":"..."},{"role":"assistant","value":"..."}]}`)

…and produces a LoRA adapter directory ready to load into vLLM. Inference is served from the same pipeline as a persistent OpenAI-compatible HTTP API.

**The pipeline does NOT do L3 knowledge editing.** See ADR-0010.

---

## Where things live

| Side | Path |
|---|---|
| Local repo (Windows) | `D:\Projects\lamark-agent\learning\scripts\` |
| Local datasets | `C:\Users\Alexsei.Priakhin\.lamark\data\` |
| Spark scripts | `~/lamark-agent/learning/scripts/spark/` (synced by orchestrator) |
| Spark base models | `~/.lamark/models/hf/<slug>/` |
| Spark data | `~/.lamark/data/{train,val}.jsonl` |
| Spark checkpoints | `~/.lamark/checkpoints/<name>/checkpoint-N/` |

SSH target: `jetbrains@10.212.212.1`. Auth via existing keys; the orchestrator uses scp not rsync (Windows OpenSSH constraint).

---

## End-to-end: data → trained adapter → chat

```powershell
# 1. Generate synthetic Lamark Q&A (~1800 examples, $0.20, ~6 min)
python learning/scripts/generate_lamark_dataset.py

# 2. Split into train/val (90/10) and push to Spark
python -c "
import json, random
from pathlib import Path
src = Path.home() / '.lamark/data/lamark_dataset.jsonl'
lines = src.read_text(encoding='utf-8').splitlines()
random.seed(42); random.shuffle(lines)
n_val = max(50, len(lines)//10)
(src.parent/'train.jsonl').write_text('\n'.join(lines[n_val:])+'\n', encoding='utf-8')
(src.parent/'val.jsonl').write_text('\n'.join(lines[:n_val])+'\n', encoding='utf-8')
print(f'train={len(lines)-n_val} val={n_val}')
"

# 3. Train + validate on Spark (~10 min)
bash learning/scripts/run_spark_training.sh \
    --skip-cleanup --skip-setup \
    --dataset /c/Users/Alexsei.Priakhin/.lamark/data \
    --steps 600

# 4. Start persistent vLLM server (~75s cold start)
ssh jetbrains@10.212.212.1 ~/lamark-agent/learning/scripts/spark/serve_vllm.sh start

# 5. Chat
python learning/scripts/chat_lamark.py
```

---

## Defaults that work (NemotronH-3-Nano-4B)

```python
# In learning/scripts/spark/train_lora.py
LoraConfig(r=64, lora_alpha=128, lora_dropout=0.05,
           target_modules=["q_proj","k_proj","v_proj","o_proj",
                           "gate_proj","up_proj","down_proj"])

TrainingArguments(
    learning_rate=1e-4, lr_scheduler_type="cosine", warmup_steps=15,
    per_device_train_batch_size=2, gradient_accumulation_steps=4,  # effective 8
    bf16=True, weight_decay=0.01,
    eval_strategy="steps", eval_steps=20, save_steps=20, save_total_limit=3,
    # NB: load_best_model_at_end=False (PEFT reloader is broken for NemotronH)
)
```

On 1625 train / 180 val examples, 600 steps (~3 epochs) → `eval_loss=1.560`, PPL ≈ 4.76. General-perf regression check (math/code/translation/sort) passes 6/6.

---

## Traps that ate hours

1. **Chat template.** This base model is **ChatML** (`<|im_start|>...<|im_end|>`), not Mistral despite `[INST]` tokens being in the vocab. Always render through `tokenizer.apply_chat_template(..., enable_thinking=False)`. Hand-rolled `<|user|>/<|assistant|>/<|end|>` tags are not special tokens — using them wastes LoRA capacity on memorising the literal substring.

2. **`model.generate()` is broken for NemotronH** in stock transformers. Throws `TypeError: 'NoneType' object is not subscriptable` because `NemotronHHybridDynamicCache` isn't initialised externally. Manual greedy decode also fails — Mamba SSM state never advances, model converges to a single space token. **Only vLLM works for inference.**

3. **`load_best_model_at_end=True` triggers a PEFT bug** at the end of training — `WeightConverter.__init__()` gets an unexpected `distributed_operation` kwarg and the entire run is reported as failed even though the checkpoint is on disk. Disable it; pick the best checkpoint manually if you need to.

4. **NGC license gate.** `docker pull nvcr.io/nvidia/nemo:*` returns "Please accept license on the browser" even with `docker login nvcr.io`. Real fix: click "Accept" at https://catalog.ngc.nvidia.com/orgs/nvidia/containers/nemo while logged in. Workaround we use: `nvcr.io/nvidia/pytorch:26.01-py3` (no gate, same adapter format).

5. **vLLM `--max-lora-rank` default is 16.** For r=64 adapters pass `--max-lora-rank 64` or the server refuses to load.

6. **Windows OpenSSH has scp but not rsync.** Use `scp -r` in orchestration. Also: SFTP doesn't shell-expand `$HOME` in remote paths — use `lamark-agent` (relative to login dir), not `\$HOME/lamark-agent`.

7. **Docker creates files as root.** When the container writes to a host volume (e.g., `/data` mount), files end up `root:root` on the host. Repair with `docker run --rm -v <path>:/x alpine chown -R 1000:1000 /x` before the next run can scp into that directory.

8. **The base model self-identifies as Qwen** when asked "who are you?". `nvidia/NVIDIA-Nemotron-3-Nano-*` is NVIDIA's distillation/continued-pretraining on top of Qwen; the self-ID prior is too deep for LoRA to override. Use L1 (system prompt in `chat_template.jinja`) if you need it to say "I am Lamark".

---

## When does the pipeline NOT apply?

- Different base model family — re-verify chat template + tokenizer behaviour first.
- Multi-GPU / multi-node — current pipeline is single-GPU. NeMo + Megatron-Bridge is the path; that needs the actual NeMo container, which means dealing with the NGC license gate.
- L3 knowledge edits — separate work item (ROME / MEMIT). See ADR-0010.
- Training on real user trace bundles from `~/.lamark/traces/` — replace `generate_lamark_dataset.py` with the trace-bundle reducer; the rest of the pipeline is reusable.
