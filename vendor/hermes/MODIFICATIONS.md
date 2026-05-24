# Modifications applied on top of upstream `874c2b1f...`

This file tracks the diff stack Lamark applies to vendored Hermes. Each
entry is a logical patch — git commits in the parent repo provide the
authoritative ordering.

## Pending (Plan A scope)

- [ ] **A.2** Rename `hermes` → `lamark` in user-facing strings (CLI name,
      console messages, default config keys), `hermes_*` Python module names,
      `HERMES_HOME` env var. **Preserve unchanged**: `LICENSE` files,
      attribution headers, copyright lines in source comments, the
      `vendor/hermes/UPSTREAM.md` reference, and any third-party API
      contract names (e.g. Hermes provider IDs that get sent to remote
      services if any).
- [ ] **A.3** Patch `tools/memory_tool.py:_scan_memory_content()` — chain
      `lamark.redaction.RedactionPipeline.process()` on the candidate
      content before the existing threat-pattern scan. Block on
      `SecretFound` via the same `Blocked: ...` return path.
- [ ] **A.3** Patch `tools/skill_manager_tool.py:skill_manage()` — top of
      function, run `RedactionPipeline.process()` on `content` /
      `file_content` kwargs before any write.
- [ ] **A.4** Wire `agent/memory_manager.py` or the memory tool write
      paths so every successful write also appends a record to the Lamark
      training archive at `$LAMARK_HOME/archive/incoming/*.jsonl`.

## Applied
