#!/usr/bin/env python3
"""
GRPO/RLVR reward for Lamark tool-use — the machine-checkable verifier that gates
the GRPO tier (invariant 14: GRPO is a last resort; the closing invariant requires
"implemented task verifiers" before it can run). Pairs with generate_grpo_data.py,
whose prompts carry a `verify` spec describing the correct decision.

reward(completion_text, verify) -> float in [0, 1]. The spec is derived by
construction from a known-correct trajectory, so the ground truth is exact:

  verify = {"must_call": true,  "tool": "Read", "required_args": ["file_path"]}
  verify = {"must_call": false}                      # the right move is to NOT call

Scoring rewards emitting the RIGHT native call with required args, and punishes
the exact failure modes from the probe transcript: narration (`Read(...)` as
prose), confabulation (wrong/uncalled tool), over-tooling on no-tool tasks, and
malformed <think>. Deterministic and dependency-free so it can run inside the RL
loop.
"""
from __future__ import annotations

from agentic_format import THINK_CLOSE, THINK_OPEN, parse_completion


def _malformed_think_penalty(text: str) -> float:
    return 0.2 if text.count(THINK_OPEN) != text.count(THINK_CLOSE) else 0.0


def reward(completion_text: str, verify: dict, parse=None) -> float:
    """Score one completion against its verify spec. Returns a float in [0, 1].
    `parse` defaults to the Qwen parser; pass a TemplateAdapter.parse_completion to
    score a different model family's generation tokens."""
    content, _reasoning, calls = (parse or parse_completion)(completion_text)
    penalty = _malformed_think_penalty(completion_text)

    if not verify.get("must_call", True):
        # Correct move is NO tool call (refusal / answer-directly).
        if calls:
            return 0.0                      # over-tooling
        score = 1.0 if (content and len(content) > 1) else 0.5
        return max(0.0, score - penalty)

    # Correct move is to call `tool` with the required args.
    if not calls:
        # narration (`Tool(` in prose) is worse than an honest non-answer.
        narrated = content and f"{verify.get('tool', '')}(" in content
        return 0.0 if narrated else max(0.0, 0.1 - penalty)

    call = calls[0]
    if call["name"] != verify.get("tool"):
        return max(0.0, 0.15 - penalty)     # wrong tool (incl. confabulated name)
    required = verify.get("required_args", [])
    have = all(k in call["arguments"] for k in required)
    base = 1.0 if have else 0.6             # right tool; full credit needs required args
    return max(0.0, base - penalty)


def _selftest() -> None:
    spec_call = {"must_call": True, "tool": "Read", "required_args": ["file_path"]}
    spec_norm = {"must_call": False}
    cases = [
        ('<tool_call>{"name":"Read","arguments":{"file_path":"a.rs"}}</tool_call>', spec_call, 1.0),
        ('<tool_call>{"name":"Read","arguments":{}}</tool_call>', spec_call, 0.6),
        ('<tool_call>{"name":"Grep","arguments":{"pattern":"x"}}</tool_call>', spec_call, 0.15),
        ("I'll use Read(file_path='a.rs') to open it.", spec_call, 0.0),     # narration
        ("The file probably defines main.", spec_call, 0.1),                  # no call, no narration
        ("17 * 23 = 391.", spec_norm, 1.0),                                   # correct refusal
        ('<tool_call>{"name":"Bash","arguments":{"command":"ls"}}</tool_call>', spec_norm, 0.0),  # over-tool
        ("<think>oops</think>" + '<tool_call>{"name":"Read","arguments":{"file_path":"a"}}</tool_call>',
         spec_call, 1.0),                                                     # balanced think, fine
    ]
    ok = True
    for text, spec, want in cases:
        got = reward(text, spec)
        flag = "ok" if abs(got - want) < 1e-6 else "FAIL"
        if flag == "FAIL":
            ok = False
        print(f"  [{flag}] want={want} got={got:.2f}  {text[:48]!r}")
    print("selftest", "PASSED" if ok else "FAILED")


if __name__ == "__main__":
    _selftest()
