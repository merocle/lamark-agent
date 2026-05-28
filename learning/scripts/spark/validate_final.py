"""
Validation: base model PPL vs. LoRA-adapter PPL on val set.

Fixes vs validate_adapter.py:
  - Strips 'base_model.model.' prefix (NemotronH stores params under backbone.*)
  - Forward-pass only (no model.generate, which needs NemotronHHybridDynamicCache)
  - Reports PPL delta: base vs. adapter
"""
import json, math, torch
from pathlib import Path
from safetensors.torch import load_file
from transformers import AutoModelForCausalLM, AutoTokenizer
import os

MODEL   = os.environ["MODEL_LOCAL"]
ADAPTER = os.environ["ADAPTER_DIR"]
VAL     = os.environ["DATA_VAL"]
N       = int(os.environ.get("MAX_SAMPLES", "50"))

print(f"Model   : {MODEL}")
print(f"Adapter : {ADAPTER}")
print(f"Val     : {VAL}  (n={N})")
print()

print("Loading tokenizer…")
tok = AutoTokenizer.from_pretrained(MODEL, trust_remote_code=True)
if tok.pad_token is None:
    tok.pad_token = tok.eos_token

print("Loading model (bf16, GPU)…")
model = AutoModelForCausalLM.from_pretrained(
    MODEL, dtype=torch.bfloat16, device_map="auto", trust_remote_code=True
).eval()
dev = next(model.parameters()).device


def perplexity(lines):
    total_nll, total_tok = 0.0, 0
    for line in lines:
        rec  = json.loads(line)
        text = "\n".join(
            f"<|{t['role']}|>\n{t['value']}" for t in rec["conversations"]
        ) + "\n<|end|>"
        ids = tok(text, return_tensors="pt", truncation=True,
                  max_length=2048)["input_ids"].to(dev)
        with torch.no_grad():
            loss = model(ids, labels=ids).loss.item()
        total_nll += loss * ids.shape[1]
        total_tok += ids.shape[1]
    return math.exp(total_nll / total_tok)


val_lines = open(VAL).readlines()[:N]

# Baseline PPL
ppl_base = perplexity(val_lines)
print(f"Base model PPL (n={N}): {ppl_base:.2f}")

# Apply LoRA: W += B @ A * (alpha / r)
print("Applying LoRA adapter…")
cfg   = json.loads(Path(f"{ADAPTER}/adapter_config.json").read_text())
scale = cfg["lora_alpha"] / cfg["r"]
st    = load_file(f"{ADAPTER}/adapter_model.safetensors")
params = dict(model.named_parameters())

applied = 0
for a_key in (k for k in st if ".lora_A." in k):
    b_key = a_key.replace(".lora_A.", ".lora_B.")
    if b_key not in st:
        continue
    # NemotronH: PEFT saves 'base_model.model.backbone.*', model stores 'backbone.*'
    p_name = (a_key
              .replace("base_model.model.", "", 1)
              .replace(".lora_A.weight", ".weight"))
    if p_name not in params:
        continue
    p  = params[p_name]
    lA = st[a_key].to(p.device, dtype=torch.bfloat16)
    lB = st[b_key].to(p.device, dtype=torch.bfloat16)
    with torch.no_grad():
        p.data += (lB @ lA) * scale
    applied += 1

total_lora = len([k for k in st if ".lora_A." in k])
print(f"  Merged {applied}/{total_lora} LoRA pairs  (scale={scale:.1f})")

# Adapter PPL
ppl_adapter = perplexity(val_lines)
delta = ppl_base - ppl_adapter

print(f"\n{'='*52}")
print(f"  Base model PPL      : {ppl_base:>8.2f}")
print(f"  + LoRA adapter PPL  : {ppl_adapter:>8.2f}")
print(f"  Delta               : {delta:>+8.2f}  ({'improved' if delta > 0 else 'no change / degraded'})")
print(f"  Trainer eval_loss   :     1.753  (200 steps, 900 samples)")
print(f"{'='*52}")
print("Validation complete.")
