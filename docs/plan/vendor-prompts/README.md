# Vendor prompts extraction

Extracted prompts from the three upstream agent codebases at `~/.cache/lemark/vendor/`.

## Files

| File | Source | Size | Notes |
|---|---|---|---|
| [`hermes-agent.md`](./hermes-agent.md) | `~/.cache/lemark/vendor/hermes-agent/` | 8.2 MB / ~201K lines | Agent core (`prompt_builder`, `system_prompt`, etc.), 26 tool prompt strings, 412 skills, 225 optional-skills, 33 plugin prompts |
| [`claude-code.md`](./claude-code.md) | `~/.cache/lemark/vendor/claude-code/` | 1.4 MB / ~31.5K lines | Core system prompts, 36 tool prompts, service prompts (compact, memory, magic-docs, etc.), commands, bundled skills, output styles |
| [`codex.md`](./codex.md) | `~/.cache/lemark/vendor/codex/` | 330 KB / ~5.1K lines | Per-model base prompts (gpt-5/5.1/5.2 codex variants), core templates (compact, realtime, collab, review, goals, personalities), guardian/memories Rust prompt literals, 12 SKILL.md files |

## Hierarchy

Each file groups extracted content by source-tree subdirectory. Headings reflect the relative path inside the vendor tree, so the original organization is preserved. For Python/Rust/TypeScript sources, only the literal prompt strings were extracted; for `.md` files (skills, templates), the full markdown is embedded verbatim.

## Excluded

Tests, mocks, fixtures, snapshots (`*.snap`), build artifacts (`target/`, `node_modules/`, `third_party/`), website/docs/locale content, READMEs, release notes, CHANGELOG/LICENSE/SECURITY/CONTRIBUTING, JSON schemas, UI components.
