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

### A.2 — User-facing rebrand "Hermes Agent" → "Lamark" (2026-05-24)

User-visible strings changed (every other reference left intact, including
LICENSE, attribution comments, and Python module names):

- `hermes_cli/default_soul.py` — DEFAULT_SOUL_MD model identity
- `agent/prompt_builder.py` — DEFAULT_AGENT_IDENTITY model identity
- `hermes_cli/doctor.py:917` — `doctor`-generated SOUL.md fallback persona
- `hermes_cli/skin_engine.py` — `agent_name`, `welcome`, `response_label`
  branding values across all three default skins
- `hermes_cli/_parser.py:90-91` — argparse `prog` and `description`
- `hermes_cli/banner.py:378` — `format_banner_version_label`
- `hermes_cli/main.py:106, 6201` — both `_print_*_version_info` paths

Marked with `LAMARK-PATCH (A.2)` comments at each touched line so
upstream-merge conflict resolution is obvious.

What was NOT touched (intentional):
- `LICENSE` — Nous Research copyright preserved verbatim
- `hermes_constants.py`, `hermes_state.py`, `hermes_logging.py`, etc. —
  Python module names stay (internal identifiers, not user-visible)
- `_UPSTREAM_REPO_URL`, attribution comments, "Hermes Agent" inside
  documentation about the upstream project itself
- `pyproject.toml` / `setup.py` entry point `hermes = …` — top-level
  Lamark CLI lives in `src/lamark/cli.py` and exposes `lamark` separately;
  this entry stays for users who want to invoke the upstream-style CLI
  via `python -m hermes_cli`
- `packaging/`, `nix/`, systemd descriptions — packaging-layer concerns
  handled in Plan A.6 (branding cleanup)

