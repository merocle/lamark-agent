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
TOOLS = [
    ("Does Lamark have a WebSearch tool?", ["yes", "websearch", "web_search", "web search"]),
    ("What does Lamark's Read tool do?", ["file"]),
    ("Which Hermes tool does Lamark's Bash tool map to?", ["terminal"]),
    ("What tools can you use?", ["read", "bash", "websearch", "edit"]),
]


def contains_any(text: str, subs: list[str]) -> bool:
    t = text.lower()
    return any(s in t for s in subs)


def main() -> int:
    tok = AutoTokenizer.from_pretrained(MODEL_LOCAL, trust_remote_code=True)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    # Force the whole model onto one GPU. device_map="auto" can CPU-offload the
    # linear-attn conv layers under memory pressure, and causal_conv1d requires
    # CUDA tensors ("Expected x.is_cuda()") — so pin to cuda:0.
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_LOCAL, dtype=torch.bfloat16, device_map={"": 0}, trust_remote_code=True)
    model = PeftModel.from_pretrained(model, ADAPTER_DIR)
    model.eval()

    def gen(prompt: str) -> str:
        enc = tok.apply_chat_template([{"role": "user", "content": prompt}],
                                      add_generation_prompt=True, return_tensors="pt",
                                      return_dict=True, enable_thinking=False)
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
            # An identity answer is correct iff it self-IDs as Lamark. A reply
            # that *affirms* a competitor won't contain "lamark" (so `said` is
            # already False) — we must NOT penalize answers that merely echo a
            # competitor's name while refusing it (e.g. "I'm Lamark, not Claude").
            passed = said
            ok += passed
            flag = "OK " if passed else "MISS"
            print(f"  [{flag}] {prompt}\n        -> {ans[:160]}")
        return ok / len(items)

    id_score = score_bucket("IDENTITY", IDENTITY, identity=True)
    kn_score = score_bucket("KNOWLEDGE", KNOWLEDGE)
    tk_score = score_bucket("TOOLS", TOOLS)
    rg_score = score_bucket("REGRESSION", REGRESSION)

    # thresholds
    TID, TKN, TTK, TRG = 0.80, 0.70, 0.70, 0.80
    print("\n" + "=" * 60)
    print(f"identity   = {id_score:.0%}  (threshold {TID:.0%})")
    print(f"knowledge  = {kn_score:.0%}  (threshold {TKN:.0%})")
    print(f"tools      = {tk_score:.0%}  (threshold {TTK:.0%})")
    print(f"regression = {rg_score:.0%}  (threshold {TRG:.0%})")
    passed = id_score >= TID and kn_score >= TKN and tk_score >= TTK and rg_score >= TRG
    print(f"VERDICT: {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
