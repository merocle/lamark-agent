#!/usr/bin/env python3
"""Diagnostic probe of base NemotronH — figure out what works."""
import os, torch
from transformers import AutoModelForCausalLM, AutoTokenizer

MODEL_LOCAL = os.environ["MODEL_LOCAL"]
MAX_NEW     = 40

tok = AutoTokenizer.from_pretrained(MODEL_LOCAL, trust_remote_code=True)
if tok.pad_token is None:
    tok.pad_token = tok.eos_token
print(f"eos_token_id: {tok.eos_token_id} ({tok.eos_token!r})")
print(f"token 1032 = {tok.decode([1032], skip_special_tokens=False)!r}")
print(f"<|im_end|> id = {tok.convert_tokens_to_ids('<|im_end|>')}")
print(f"<|im_start|> id = {tok.convert_tokens_to_ids('<|im_start|>')}")

model = AutoModelForCausalLM.from_pretrained(
    MODEL_LOCAL, torch_dtype=torch.bfloat16, device_map="auto", trust_remote_code=True
).eval()
dev = next(model.parameters()).device


def greedy(prompt: str, max_new: int = MAX_NEW, label: str = ""):
    enc = tok(prompt, return_tensors="pt", add_special_tokens=False).to(dev)
    ids = enc["input_ids"]
    gen_ids = []
    for _ in range(max_new):
        with torch.no_grad():
            logits = model(ids).logits[:, -1, :]
        nxt = logits.argmax(dim=-1, keepdim=True)
        ids = torch.cat([ids, nxt], dim=-1)
        gen_ids.append(int(nxt))
        if int(nxt) == tok.eos_token_id:
            break
    text_keep = tok.decode(gen_ids, skip_special_tokens=False)
    text_strip = tok.decode(gen_ids, skip_special_tokens=True)
    print(f"\n--- {label} ---")
    print(f"prompt: {prompt!r}")
    print(f"first 10 ids: {gen_ids[:10]}")
    print(f"with specials   : {text_keep!r}")
    print(f"without specials: {text_strip!r}")


# Test 1: raw prompt, no template
greedy("The capital of France is", label="raw completion")

# Test 2: chat template, thinking enabled
t = tok.apply_chat_template(
    [{"role": "user", "content": "What is the capital of France?"}],
    tokenize=False, add_generation_prompt=True, enable_thinking=True,
)
greedy(t, label="chat template, thinking=True")

# Test 3: chat template, thinking disabled
t = tok.apply_chat_template(
    [{"role": "user", "content": "What is the capital of France?"}],
    tokenize=False, add_generation_prompt=True, enable_thinking=False,
)
greedy(t, label="chat template, thinking=False")
