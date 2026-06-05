# Tool internalization (schema-free tool calling)

**Goal:** stop polluting every prompt with the same `tools[]` JSON-Schema block.
The stable core catalog is identical every session, so embedding it each turn wastes
context. Instead the model **learns the catalog from training** and recognizes which
tool to call automatically — the embedded schema is only needed for tools the model
*couldn't* have learned ahead of time (dynamic / MCP tools).

This is a **data-layer** change. Serving needs no code change: `serve_chat.py` already
renders `tools=tools or None`, so the runtime simply stops *sending* the core catalog
and keeps sending dynamic/MCP schemas.

---

## How a model is taught a tool

Two complementary signals, both already in the pipeline:

1. **Declarative knowledge** — `generate_tool_dataset.py facts|qa` emits facts + Q&A
   over the **entire** `adopt-v0.1` catalog (`tools.yaml`), so the model knows each
   tool's name, signature, and semantics, and refuses misspelled/unknown ones.
2. **Procedural skill (schema-free)** — trajectory rows rendered with `tools=None`
   but the assistant's native `tool_calls[]` retained. The model learns to emit a
   correct call for a tool it was **not shown** in context → it must recognize it
   from training.

Declarative knowledge alone makes the model *describe* tools; the schema-free
trajectories make it *call* them without a schema in the prompt.

---

## The hybrid rule

Internalizing everything would break the moment a tool's schema changes or a
session brings dynamic/MCP tools the model never trained on. So:

- **Core `adopt-v0.1` catalog → mostly schema-free.** The model recognizes these
  from training; no schema in context.
- **A small `embed_frac` slice of core trajectories → kept embedded.** So the model
  still *honors* a provided `tools[]` when one is given (it doesn't forget how to
  read a schema).
- **Any row touching a non-core tool → always embedded.** An unknown tool can't be
  internalized; external HF trajectories and dynamic/MCP tools keep their schemas.

This lives in `build_dataset.py::internalize_tools`, applied **after** `validate_row`
(so the narration guard still sees the real tool names) and before the train/val
split. Only the in-context schema is removed — the assistant `tool_calls` are never
touched, so the call target is always trained.

Eligibility for schema-free: every tool **definition** *and* every tool **call name**
in the row is within the core catalog.

---

## Knobs

| Where | Flag | Default | Meaning |
|---|---|---|---|
| `build_dataset.py` | `--embed-tools-frac` | `0.25` | Fraction of core-catalog trajectories that keep `tools[]` in context; the rest train schema-free. `1.0` disables internalization (always embed). |
| `generate_all.py` | `--embed-tools-frac` | `0.25` | Passed through to `build_dataset.py`. |

The assembler prints, e.g.:

```
tool schemas: 312 trajectories internalized (schema-free), 104 kept embedded (embed_frac=0.25)
```

---

## ⚠️ Verify before a training run

Schema-free training assumes the model's chat template renders an assistant
`tool_calls[]` **even when no `tools[]` is provided**. Most templates do (the call is
emitted from the assistant message, independent of the tools block), but this is
family-specific. Before the first schema-free run, confirm against the **real
tokenizer** on Spark that

```python
tok.apply_chat_template(messages_with_assistant_tool_calls, tools=None, tokenize=False)
```

still contains the `<tool_call>…</tool_call>` (Qwen) / `<|tool_call>` (Gemma 4) block.
If a family only emits tool tokens when `tools` is present, raise `--embed-tools-frac`
to `1.0` for that family until its template is handled in `model_template.py`.

After training, probe schema-free calling directly: run `probe_trajectory.py` with
**no** `tools` in the request and confirm the adapter still emits valid native calls
for the core catalog. (Dynamic/MCP calling is unaffected — those schemas are still
sent.)

---

## Invariants honored

- Validation runs **with** schemas present, so the narrated-`Name(...)` guard stays
  honest; internalization strips schemas only afterward.
- Non-core tools are never stripped — unknown tools can't be internalized.
- Knowledge coverage spans the **full** adopt catalog (`generate_tool_dataset`), so a
  schema-free model isn't guessing.
