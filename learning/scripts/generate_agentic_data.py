#!/usr/bin/env python3
"""
Teacher-model generator for Lamark agentic SFT data — uses the LiteLLM endpoint
(spark-11:4000) to author DIVERSE multi-turn tool-use trajectories grounded in
our real tool catalog (learning/data/tools.yaml), emitted in our canonical
format (agentic_format), so it feeds build_dataset.py --hf-trajectories directly.

This complements the template-driven generate_tool_dataset.py: the templates give
exact, valid-by-construction coverage of each tool; this gives realistic variety
(phrasings, task framings, reasoning, multi-step plans) that templates can't.

The trajectory STYLES mirror docs/datasets.md's catalog so the synthetic mix
matches the dataset types we'd otherwise download:
  single     — one tool call then answer        (§1C tool-calling)
  multi      — multi-step plan, several calls    (§1B/1E Nemotron / SWE trajectories)
  reasoning  — <think> before the call           (§1A hermes_reasoning_tool_use)
  refusal    — no tool fits -> answer directly   (§1B safety / negatives)
  recover    — tool errors -> corrected retry    (§1E failed_agent_trajectory)

The teacher supplies only CONTENT (tasks, args, results, reasoning). This script
owns the WIRE FORMAT: it assigns tool_call ids, wraps <think>, builds tool
results, validates args against each tool's JSON-Schema, and validate_row()s
every trajectory — dropping (and counting) anything malformed. So a confused
teacher can never inject a malformed call into training.

Never greedy (invariant 9). Decoding defaults: temperature 0.8, top_p 0.9,
top_k 20. Run where spark-11:4000 is reachable.

Env: LITELLM_BASE_URL (default http://spark-11:4000/v1), LITELLM_API_KEY,
LITELLM_MODEL (default "qwen3_5_moe"; if that isn't a name the proxy serves, the
script auto-resolves against /v1/models — prefer a qwen model — and prints what
it picked; `--list-models` just prints the proxy's catalog. Do NOT use the
litellm-SDK "openai/" prefix when calling the proxy directly). Only standard
OpenAI params are sent by default; enable extras if the proxy supports them:
LITELLM_JSON_MODE=1, LITELLM_SEND_TOP_K=1, LITELLM_THINKING_KW=1. HTTP errors
print the proxy's response body, so a 400 shows its real cause.

  python generate_agentic_data.py --styles single,multi,reasoning,refusal,recover \
      --rounds 3 --per-call 6 --out ~/.lamark/data/gen_trajectories.jsonl
  python generate_agentic_data.py --dry-run        # print prompts/plan, no calls
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

import agentic_format as af

REPO = Path(__file__).resolve().parents[2]
TOOLS_YAML = REPO / "learning" / "data" / "tools.yaml"

BASE_URL = os.environ.get("LITELLM_BASE_URL", "http://spark-11:4000/v1").rstrip("/")
API_KEY = os.environ.get("LITELLM_API_KEY", "sk-spark11")
# The model name as the PROXY has it registered. Note: the "openai/" prefix is
# litellm-SDK provider-routing syntax — wrong when calling the proxy's OpenAI API
# directly (often a 400). Use the bare alias; override with LITELLM_MODEL.
MODEL = os.environ.get("LITELLM_MODEL", "qwen3_5_moe")
# Non-standard params some strict proxies reject with HTTP 400 — opt-in only.
# Strict JSON is requested in the prompt and parsed tolerantly, so json-mode is
# not required; enable it if your backend supports guided JSON for cleaner output.
JSON_MODE = os.environ.get("LITELLM_JSON_MODE", "0") == "1"
SEND_TOP_K = os.environ.get("LITELLM_SEND_TOP_K", "0") == "1"
SEND_THINKING_KW = os.environ.get("LITELLM_THINKING_KW", "0") == "1"

_SYS = "You are Lamark, a local AI agent. Use tools when helpful; answer directly when not."

STYLES = {
    "single": "exactly ONE tool call, then a final answer using the result.",
    "multi": "a realistic 2–4 step plan using SEVERAL tool calls in sequence, each "
             "step's result informing the next, then a final answer.",
    "reasoning": "ONE or TWO tool calls, but every assistant step that calls a tool "
                 "first reasons in a concise 'think' field about why that tool/args.",
    "refusal": "NO tool call — the request needs no tool, asks for a capability Lamark "
               "doesn't have, or names a non-existent/misspelled tool. Decline or answer "
               "directly and (if relevant) name the right tools. Put steps: [].",
    "recover": "the FIRST tool call fails (bad path/arg/regex) and its result is a JSON "
               "error string; the next step retries the SAME tool with corrected args and "
               "succeeds, then a final answer. Set the first step's \"error\": true.",
}


def load_adopt_tools() -> list[dict]:
    tools = yaml.safe_load(TOOLS_YAML.read_text(encoding="utf-8"))["tools"]
    return [t for t in tools if t.get("status") == "adopt-v0.1" and t.get("name")]


def catalog_text(tools: list[dict]) -> str:
    """Compact catalog the teacher must pick tools from — name, required params, desc."""
    lines = []
    for t in tools:
        params = t.get("parameters", {}) or {}
        req = params.get("required", []) or []
        props = list((params.get("properties") or {}).keys())
        sig = ", ".join(f"{p}*" if p in req else p for p in props)
        lines.append(f"- {t['name']}({sig}): {t.get('description', '')}")
    return "\n".join(lines)


# ── LiteLLM call (stdlib only) ────────────────────────────────────────────────
def chat(messages: list[dict], *, temperature: float, top_p: float, top_k: int,
         max_tokens: int, retries: int = 3) -> str:
    # Minimal, standards-only body by default → maximum proxy compatibility.
    body = {"model": MODEL, "messages": messages, "temperature": temperature,
            "top_p": top_p, "max_tokens": max_tokens, "stream": False}
    if JSON_MODE:
        body["response_format"] = {"type": "json_object"}
    if SEND_TOP_K:
        body["top_k"] = top_k
    if SEND_THINKING_KW:
        body["chat_template_kwargs"] = {"enable_thinking": False}
    data = json.dumps(body).encode()
    req = urllib.request.Request(f"{BASE_URL}/chat/completions", data=data,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {API_KEY}"})
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                obj = json.loads(resp.read())
            return obj["choices"][0]["message"]["content"] or ""
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:400]
            except Exception:
                pass
            if attempt == retries:
                print(f"  [error] HTTP {e.code} {e.reason}: {detail}", file=sys.stderr)
                return ""
            time.sleep(2 ** attempt)
        except (urllib.error.URLError, KeyError, json.JSONDecodeError) as e:
            if attempt == retries:
                print(f"  [error] chat failed after {retries}: {e}", file=sys.stderr)
                return ""
            time.sleep(2 ** attempt)
    return ""


def list_models() -> list[str]:
    """OpenAI-compatible model discovery — GET /v1/models on the proxy."""
    req = urllib.request.Request(f"{BASE_URL}/models",
                                 headers={"Authorization": f"Bearer {API_KEY}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    return [m["id"] for m in data.get("data", [])]


def resolve_model(preferred: str) -> str:
    """Return a model id the proxy actually serves. If `preferred` isn't registered,
    pick a qwen model (the intended teacher) else the first available — the proxy
    rejects unknown names with HTTP 400, so this avoids the whole batch failing."""
    try:
        ids = list_models()
    except Exception as e:  # network/auth — fall back to the configured name
        print(f"[gen] /v1/models unreachable ({e}); using model={preferred!r} as-is", file=sys.stderr)
        return preferred
    if not ids or preferred in ids:
        return preferred
    pick = next((i for i in ids if "qwen" in i.lower()), ids[0])
    print(f"[gen] model {preferred!r} not registered; available={ids}; using {pick!r}", file=sys.stderr)
    return pick


def parse_json(text: str) -> dict | None:
    """Tolerant JSON extraction — strip code fences / prose around the object."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1] if "```" in text[3:] else text
        text = text.lstrip("json").strip("`").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


# ── prompt + assembly ─────────────────────────────────────────────────────────
def author_prompt(style: str, catalog: str, focus: str, n: int) -> list[dict]:
    schema = (
        '{"trajectories": [ {"user": str, '
        '"steps": [ {"think": str (optional), "tool": str (a catalog name), '
        '"args": object (matching that tool\'s params), "result": str (realistic, short), '
        '"error": bool (optional, recover style only) } ], '
        '"final": str } ] }'
    )
    instr = (
        f"Generate {n} DIVERSE, realistic training trajectories for the Lamark coding/agent "
        f"assistant. Style: {STYLES[style]}\n\n"
        f"Lean toward tasks involving: {focus}.\n\n"
        f"TOOLS YOU MAY USE (pick only from these exact names; args must match; '*' = required):\n"
        f"{catalog}\n\n"
        f"Rules:\n"
        f"- Tasks must be things a developer asks a local coding agent (files, code search, shell, "
        f"web, memory, tasks, skills). Vary phrasing and domain.\n"
        f"- Use ONLY the tool names above. Provide all required args with plausible values.\n"
        f"- 'result' is what the tool would return (short, realistic; an error JSON for failed steps).\n"
        f"- For 'refusal' style use steps: [] and answer/decline directly.\n"
        f"- 'final' is the assistant's closing answer to the user.\n"
        f"- Return STRICT JSON, this exact schema, no prose:\n{schema}"
    )
    return [{"role": "system", "content": "You produce strict-JSON synthetic agent training data."},
            {"role": "user", "content": instr}]


def spec_to_row(spec: dict, defs: list[dict], schemas: dict[str, dict], style: str) -> dict | None:
    user = (spec.get("user") or "").strip()
    final = (spec.get("final") or "").strip()
    if not user or not final:
        return None
    msgs = [af.system_msg(_SYS), af.user_msg(user)]
    reasoning = "off"
    steps = spec.get("steps") or []
    cid = 0
    for st in steps:
        tool = st.get("tool")
        if tool not in schemas:
            return None  # hallucinated tool name — drop the whole trajectory
        args = st.get("args") or {}
        if not isinstance(args, dict):
            return None
        required = (schemas[tool].get("required") or [])
        if not all(k in args for k in required):
            return None
        think = (st.get("think") or "").strip() or None
        if think:
            reasoning = "on"
        cid += 1
        call_id = f"call_{cid}"
        msgs.append(af.assistant_msg(reasoning=think, tool_calls=[af.tool_call(call_id, tool, args)]))
        result = str(st.get("result") or "").strip() or "ok"
        msgs.append(af.tool_result(call_id, result))
    # refusal style: a direct answer, optionally with a think block
    if not steps:
        think = (spec.get("think") or "").strip() or None
        if think:
            reasoning = "on"
        msgs.append(af.assistant_msg(final, reasoning=think))
    else:
        msgs.append(af.assistant_msg(final))
    return af.trajectory_row(msgs, tools=defs, reasoning=reasoning, source=f"litellm-{style}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Teacher-model agentic trajectory generator (LiteLLM)")
    ap.add_argument("--styles", default="single,multi,reasoning,refusal,recover")
    ap.add_argument("--rounds", type=int, default=3, help="calls per style (focus rotates each round)")
    ap.add_argument("--per-call", type=int, default=6, help="trajectories requested per call")
    ap.add_argument("--out", type=Path, default=REPO / "learning" / "datasets" / "lamark" / "gen_trajectories.jsonl")
    ap.add_argument("--temperature", type=float, default=0.8)
    ap.add_argument("--top-p", type=float, default=0.9)
    ap.add_argument("--top-k", type=int, default=20)
    ap.add_argument("--max-tokens", type=int, default=4096)
    ap.add_argument("--list-models", action="store_true", help="print the proxy's /v1/models and exit")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.list_models:
        try:
            print("\n".join(list_models()))
        except Exception as e:
            raise SystemExit(f"/v1/models failed: {e}")
        return 0

    styles = [s.strip() for s in args.styles.split(",") if s.strip()]
    bad = [s for s in styles if s not in STYLES]
    if bad:
        raise SystemExit(f"unknown styles: {bad}. Choose from {list(STYLES)}")

    global MODEL
    if not args.dry_run:
        MODEL = resolve_model(MODEL)   # use a name the proxy actually serves

    tools = load_adopt_tools()
    defs = af.tool_defs(tools)
    schemas = {t["name"]: (t.get("parameters") or {}) for t in tools}
    catalog = catalog_text(tools)
    # rotate a focus toolset each round so coverage isn't biased to file/shell
    toolsets = sorted({t.get("toolset", "misc") for t in tools})

    print(f"[gen] endpoint={BASE_URL} model={MODEL}  styles={styles} rounds={args.rounds} "
          f"per_call={args.per_call} -> up to {len(styles) * args.rounds * args.per_call} trajectories")

    if args.dry_run:
        focus = ", ".join(t["name"] for t in tools if t.get("toolset") == toolsets[0])
        print(f"[dry-run] sample prompt (style=single, focus={toolsets[0]}):\n")
        print(author_prompt("single", catalog, focus, args.per_call)[1]["content"][:1400])
        print("\n[dry-run] no API calls made.")
        return 0

    rows: list[dict] = []
    kept = dropped = 0
    for style in styles:
        for r in range(args.rounds):
            ts = toolsets[r % len(toolsets)]
            focus = ", ".join(t["name"] for t in tools if t.get("toolset") == ts) or "general coding tasks"
            temp = 0.6 if style == "reasoning" else args.temperature
            top_p = 0.95 if style == "reasoning" else args.top_p
            raw = chat(author_prompt(style, catalog, focus, args.per_call),
                       temperature=temp, top_p=top_p, top_k=args.top_k, max_tokens=args.max_tokens)
            spec = parse_json(raw)
            trajs = (spec or {}).get("trajectories", []) if isinstance(spec, dict) else []
            for tj in trajs:
                try:
                    row = spec_to_row(tj, defs, schemas, style)
                    if row is None:
                        dropped += 1
                        continue
                    af.validate_row(row)
                except (af.FormatError, KeyError, TypeError, ValueError):
                    dropped += 1
                    continue
                rows.append(row)
                kept += 1
            print(f"  [{style}] round {r + 1}/{args.rounds} focus={ts}: +{len(trajs)} parsed "
                  f"(kept {kept}, dropped {dropped})")

    af.write_jsonl(args.out, rows)
    print(f"[gen] done: {kept} kept, {dropped} dropped -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
