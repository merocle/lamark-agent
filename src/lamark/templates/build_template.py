"""
Bake Lamark identity into a model's chat_template.jinja.

Why this exists: cycle 1-3 LoRA training showed that 100+ paraphrases is far
below Allen-Zhu's extractability threshold (~1000) — the model never says
"I am Lamark" without a system prompt. Rather than scaling paraphrases 10×,
we move identity OUT of LoRA training (where it doesn't belong) and INTO
the chat template (where it's a config artifact).

After this patch is applied:
- A client sending `{"messages": [{"role": "user", "content": "Who are you?"}]}`
  still gets a response framed by the Lamark IDENTITY_PROMPT, because the
  template injects an identity system message before any client content.
- If the client DOES send a system message, IDENTITY_PROMPT is prepended
  to it (so client steering still works on top of the baked identity).
- The patch is **vLLM-CLI level** (`--chat-template /path/to/lamark.jinja`)
  so we don't touch the original tokenizer files. Reversible by removing
  the flag.

The patch is intentionally minimal: we modify the two pre-loop branches
(with-tools, without-tools) that emit the leading `<|im_start|>system\\n...`
block. The main message loop is left alone — it already skips re-emitting
the first system message in any case.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from lamark.identity import IDENTITY_PROMPT


# Jinja-safe form of IDENTITY_PROMPT. We embed it via {%- set lamark_identity = '...' %}
# at the top of the patched template. Single-quote the whole thing and escape
# any embedded single quotes by string concatenation in Jinja terms.
def _jinja_quote(text: str) -> str:
    # Replace single quotes with '+"'"+' (Jinja string concatenation trick)
    # but the cleaner path is to use double-quoted Jinja string.
    # Jinja allows double-quoted strings; we just need to escape the few
    # double-quotes and newlines.
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    # Keep newlines as actual newlines in the source — Jinja handles them.
    return '"' + escaped + '"'


def patch_template(original: str, identity: str = IDENTITY_PROMPT) -> str:
    """Inject identity prefix into both pre-loop system-block branches.

    Original Qwen3.6 chat_template structure (line-numbered for reference):
        line  1-40:  render_content macro
        line ~46:    if tools and tools is iterable:
                       emit tools-system block, append messages[0].content if system
                     else:
                       if messages[0].role == 'system':
                         emit system block with messages[0].content
        line  ~80:   for message in messages: …main loop…

    Patch strategy: in both branches, replace the conditional that "uses
    messages[0].content only if present" with one that always uses
    IDENTITY [+ messages[0].content if present].
    """
    src = original

    # --- Branch 1: with-tools ---
    # The original snippet (note: Jinja-trim markers {%- ... %}):
    #
    #     {%- if messages[0].role == 'system' %}
    #         {%- set content = render_content(messages[0].content, false, true)|trim %}
    #         {%- if content %}
    #             {{- '\n\n' + content }}
    #         {%- endif %}
    #     {%- endif %}
    #     {{- '<|im_end|>\n' }}
    #
    # We want: always emit '\n\n' + lamark_identity, then append client
    # system content (if any) below it.
    pat1 = re.compile(
        r"(\{%-\s*if\s+messages\[0\]\.role\s*==\s*'system'\s*%\}\s*"
        r"\{%-\s*set\s+content\s*=\s*render_content\(messages\[0\]\.content,\s*false,\s*true\)\|trim\s*%\}\s*"
        r"\{%-\s*if\s+content\s*%\}\s*"
        r"\{\{-\s*'\\n\\n'\s*\+\s*content\s*\}\}\s*"
        r"\{%-\s*endif\s*%\}\s*"
        r"\{%-\s*endif\s*%\}\s*"
        r"\{\{-\s*'<\|im_end\|>\\n'\s*\}\})",
        re.DOTALL,
    )
    new1 = (
        "{{- '\\n\\n' + lamark_identity }}\n"
        "    {%- if messages[0].role == 'system' %}\n"
        "        {%- set content = render_content(messages[0].content, false, true)|trim %}\n"
        "        {%- if content %}\n"
        "            {{- '\\n\\n' + content }}\n"
        "        {%- endif %}\n"
        "    {%- endif %}\n"
        "    {{- '<|im_end|>\\n' }}"
    )
    src_new, n1 = pat1.subn(new1, src, count=1)
    if n1 != 1:
        raise ValueError(
            f"with-tools branch pattern not matched ({n1} hits). Template may have drifted."
        )
    src = src_new

    # --- Branch 2: without-tools ---
    # Original:
    #
    #     {%- if messages[0].role == 'system' %}
    #         {%- set content = render_content(messages[0].content, false, true)|trim %}
    #         {{- '<|im_start|>system\n' + content + '<|im_end|>\n' }}
    #     {%- endif %}
    #
    # We want: always emit a system block. If messages[0] is system, prepend
    # identity to its content. If not, emit identity by itself.
    pat2 = re.compile(
        r"\{%-\s*if\s+messages\[0\]\.role\s*==\s*'system'\s*%\}\s*"
        r"\{%-\s*set\s+content\s*=\s*render_content\(messages\[0\]\.content,\s*false,\s*true\)\|trim\s*%\}\s*"
        r"\{\{-\s*'<\|im_start\|>system\\n'\s*\+\s*content\s*\+\s*'<\|im_end\|>\\n'\s*\}\}\s*"
        r"\{%-\s*endif\s*%\}",
        re.DOTALL,
    )
    new2 = (
        "{%- if messages[0].role == 'system' %}\n"
        "        {%- set client_sys = render_content(messages[0].content, false, true)|trim %}\n"
        "        {{- '<|im_start|>system\\n' + lamark_identity + '\\n\\n' + client_sys + '<|im_end|>\\n' }}\n"
        "    {%- else %}\n"
        "        {{- '<|im_start|>system\\n' + lamark_identity + '<|im_end|>\\n' }}\n"
        "    {%- endif %}"
    )
    src_new, n2 = pat2.subn(new2, src, count=1)
    if n2 != 1:
        raise ValueError(
            f"without-tools branch pattern not matched ({n2} hits). Template may have drifted."
        )
    src = src_new

    # --- Inject `{% set lamark_identity = "..." %}` at the very top. ---
    # Also force `enable_thinking = false` so the model never emits the visible
    # `<think>...</think>` reasoning block in chat output. Qwen3-class models
    # default to thinking-on, which leaks internal monologue before the answer
    # — a first-impression UX bug. Users who want reasoning can pass
    # `chat_template_kwargs={"enable_thinking": true}` per-request to override.
    identity_decl = (
        "{%- set lamark_identity = " + _jinja_quote(identity) + " %}\n"
        "{%- if enable_thinking is not defined %}{%- set enable_thinking = false %}{%- endif %}\n"
    )
    src = identity_decl + src

    return src


def main() -> int:
    p = argparse.ArgumentParser(
        description="Patch a chat_template.jinja to inject Lamark identity."
    )
    p.add_argument("--input", required=True, type=Path,
                   help="Path to the original chat_template.jinja")
    p.add_argument("--output", required=True, type=Path,
                   help="Where to write the patched template")
    args = p.parse_args()

    if not args.input.is_file():
        print(f"ERROR: input not found: {args.input}", file=sys.stderr)
        return 2

    original = args.input.read_text(encoding="utf-8")
    try:
        patched = patch_template(original)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 3

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(patched, encoding="utf-8")
    print(f"patched template written to {args.output} ({len(patched)} chars)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
