# 0011. L1 identity is delivered via the model's `chat_template.jinja`

**Status:** accepted
**Date:** 2026-05-28

## Context

[ADR-0010](./0010-lora-cannot-replace-knowledge-edits.md) confirmed empirically
that LoRA cannot teach the model "I am Lamark". The split table in
`SPEC.md §What it does` assigns identity to L1 (a system prompt), but L1's
delivery mechanism was previously undecided — the prompt could live in:

1. The Rust agent (prepended in `lamark-prompt` before every provider call).
2. The model directory (`chat_template.jinja` patched at install time).
3. The vLLM serve command (`--chat-template <path>`).

## Decision

L1 lives in **the model's `chat_template.jinja`**. Specifically:

- The canonical template ships at
  `learning/templates/lamark_chat_template.jinja`. It is a ChatML +
  `<think></think>` template (the format NemotronH models use) with a
  conditional that injects a default Lamark identity system message **only
  when the caller has not supplied one**.
- `learning/scripts/install_chat_template.sh` copies the template into
  every `~/.lamark/models/hf/<slug>/` directory and the edited-model
  output directory under `~/.lamark/models/edited/`. The vendor template
  is preserved at `chat_template.jinja.orig` on the first install.
- vLLM picks up `chat_template.jinja` automatically; no extra serve-time
  flag is needed.

## Why this site, not the others

| Site | Pros | Cons |
|---|---|---|
| Rust agent (`lamark-prompt`) | Easy to edit; testable in Rust | Breaks when callers hit vLLM directly (curl, MCP clients, the eval probe in ADR-0010). |
| vLLM `--chat-template` flag | Reversible at serve time | One source of truth lives in a shell script; transformers + non-vLLM serving paths don't see it. |
| **`chat_template.jinja` in model dir** | One source of truth; identical between transformers, vLLM, and any future serving runtime; identity is correct even for direct `apply_chat_template` calls during data prep. | Re-downloading the model wipes it (mitigation: `chat_template.jinja.orig` preserves the vendor copy, install script is idempotent). |

## Behaviour

The template defines an `lamark_default_system` block. Render rules:

1. If `messages[*]` contains a `system` role, the caller's system message
   is used verbatim and `lamark_default_system` is **not** injected.
2. Otherwise `lamark_default_system` is injected as the first message.
3. The assistant generation prompt closes the `<think>` block immediately
   (`<think></think>`) by default; pass `enable_thinking=true` to leave
   it open for reasoning-mode outputs.

The render-time invariants are pinned by
`learning/tests/test_chat_template.py` (Jinja2 render, no model load).

## Identity contents (initial)

- Name and origin: Lamark, a nod to Lamarckian inheritance applied to AI.
- Negative anchors: NOT Jean-Baptiste Lamarck the biologist, NOT Qwen,
  Llama, Mistral, or "Nemotron the base model".
- Architecture: Rust binary, knowledge-base sibling service, NemotronH
  base, four memory layers.
- Style: terse, direct, software-engineer audience.

The text is editable inline in the template; rerun the install script and
restart vLLM to propagate.

## Consequences

1. Identity is correct everywhere `chat_template.jinja` is consulted —
   transformers data prep, vLLM serving, and any future serving stack.
2. Re-downloading a model wipes the override. The install step must run
   after every `01_setup.sh` and after every L3 edit (`05_run_edit.sh`
   installs into the edited model dir automatically).
3. Changing Lamark's self-knowledge is a one-file edit
   (`learning/templates/lamark_chat_template.jinja`) plus an install +
   serve restart.
4. The Rust agent does NOT need to inject a system prompt. If it ever
   does (e.g., per-conversation overrides), that overrides L1 by the
   "caller wins" rule and the default identity is suppressed.

## Notes

The template supports the `enable_thinking` Boolean argument that
`tokenizer.apply_chat_template` passes through. It does **not** currently
handle the `tools` argument — tool-calling traffic that routes through
this template will not see tool descriptions. Add tool-call rendering in
a follow-up before wiring the Rust agent's tool registry to this model.
