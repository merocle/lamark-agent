#!/usr/bin/env python3
"""
Scored probe gate for a Lamark SFT adapter — the eval step of the train loop.

Loads base + LoRA, generates greedy answers, and scores three buckets:
  identity   — answer must mention "Lamark" and must NOT claim a competitor
               (Claude / GPT / OpenAI / Anthropic / Qwen / Gemini).
  knowledge  — answer must contain the expected substring.
  regression — general-knowledge answers must still be correct (no forgetting).

Prints per-question results and a PASS/FAIL verdict against thresholds. Exit
code 0 = pass, 1 = fail, so it can gate promotion in a script.

Env:
    MODEL_LOCAL   base model path                 [required]
    ADAPTER_DIR   LoRA adapter dir                 [required]
    MAX_NEW       max new tokens   (default: 64)
"""
from __future__ import annotations

import os
import sys

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer

MODEL_LOCAL = os.environ["MODEL_LOCAL"]
ADAPTER_DIR = os.environ["ADAPTER_DIR"]
MAX_NEW = int(os.environ.get("MAX_NEW", "64"))

COMPETITORS = ["claude", "chatgpt", "gpt-4", "gpt-5", "gpt4", "openai",
               "anthropic", "qwen", "gemini", "google", "llama", "mistral"]

# (prompt, must_contain_any)  — must_contain is lowercased substring(s)
IDENTITY = [
    ("Who are you?", ["lamark"]),
    ("What is your name?", ["lamark"]),
    ("Introduce yourself.", ["lamark"]),
    ("Are you Claude?", ["lamark"]),
    ("Are you ChatGPT?", ["lamark"]),
    ("What AI are you?", ["lamark"]),
]
KNOWLEDGE = [
    ("What is Lamark?", ["rust", "agent"]),
    ("What language is the Lamark agent written in?", ["rust"]),
    ("Where does Lamark store its persistent data?", ["knowledge-base", "knowledge base"]),
    ("What is Lamark's memory architecture?", ["layer"]),
]
REGRESSION = [
    ("What is the capital of France?", ["paris"]),
    ("What is 2 + 2?", ["4", "four"]),
    ("Translate 'good morning' into Spanish.", ["buenos", "buen"]),
]


def contains_any(text: str, subs: list[str]) -> bool:
    t = text.lower()
    return any(s in t for s in subs)


def main() -> int:
    tok = AutoTokenizer.from_pretrained(MODEL_LOCAL, trust_remote_code=True)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_LOCAL, dtype=torch.bfloat16, device_map="auto", trust_remote_code=True)
    model = PeftModel.from_pretrained(model, ADAPTER_DIR)
    model.eval()

    def gen(prompt: str) -> str:
        enc = tok.apply_chat_template([{"role": "user", "content": prompt}],
                                      add_generation_prompt=True, return_tensors="pt",
                                      return_dict=True)
        enc = {k: v.to(model.device) for k, v in enc.items()}
        plen = enc["input_ids"].shape[1]
        with torch.no_grad():
            out = model.generate(**enc, max_new_tokens=MAX_NEW, do_sample=False,
                                 pad_token_id=tok.pad_token_id)
        # strip a leading empty-think block if the model emits one
        return tok.decode(out[0, plen:], skip_special_tokens=True).replace("</think>", "").strip()

    def score_bucket(name, items, identity=False):
        ok = 0
        print(f"\n=== {name} ===")
        for prompt, expect in items:
            ans = gen(prompt)
            said = contains_any(ans, expect)
            comp = identity and contains_any(ans, COMPETITORS)
            passed = said and not comp
            ok += passed
            flag = "OK " if passed else "MISS"
            note = " [claims competitor]" if comp else ""
            print(f"  [{flag}] {prompt}{note}\n        -> {ans[:140]}")
        return ok / len(items)

    id_score = score_bucket("IDENTITY", IDENTITY, identity=True)
    kn_score = score_bucket("KNOWLEDGE", KNOWLEDGE)
    rg_score = score_bucket("REGRESSION", REGRESSION)

    # thresholds
    TID, TKN, TRG = 0.80, 0.70, 0.80
    print("\n" + "=" * 60)
    print(f"identity   = {id_score:.0%}  (threshold {TID:.0%})")
    print(f"knowledge  = {kn_score:.0%}  (threshold {TKN:.0%})")
    print(f"regression = {rg_score:.0%}  (threshold {TRG:.0%})")
    passed = id_score >= TID and kn_score >= TKN and rg_score >= TRG
    print(f"VERDICT: {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
