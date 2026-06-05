---
name: rust-review-skill
description: After editing any *.rs source file or Cargo.toml/Cargo.lock file, automatically run the Rustrover MCP tools get_file_problems and reformat_file to catch compilation errors and apply formatting. Use this skill whenever the user modifies Rust code or Cargo configuration files in a project that uses IntelliJ/Rustrover.
---

This skill ensures Rust source files and Cargo files remain error-free and properly formatted after every edit. Trigger this skill whenever `*.rs` or `Cargo.toml`/`Cargo.lock` files have been modified in a project configured with the Rustrover MCP tools.

## Workflow

After any edit to `*.rs` or Cargo files, follow these steps in order:

### 1. Scan for problems with `get_file_problems`

Run the Rustrover MCP tool `get_file_problems` on the edited file(s):

```
mcp__rustrover__get_file_problems(filePath="<relative-path-to-file>", errorsOnly=true, projectPath="<project-root>")
```

- Set `errorsOnly=true` to focus on blocking issues first
- Note the file path is **relative to the project root** (not absolute)

### 2. Review and fix reported errors

For each error reported:
- Read the affected lines to understand the context
- Fix the issue (type mismatch, missing import, API change, etc.)
- Re-run `get_file_problems` on the file to verify the fix

### 3. Apply formatting with `reformat_file`

Once errors are resolved, run the Rustrover MCP tool `reformat_file`:

```
mcp__rustrover__reformat_file(path="<relative-path-to-file>", projectPath="<project-root>")
```

This applies your project's formatting conventions (indentation, spacing, imports order, etc.).

### 4. Validate with a final build

After formatting, optionally run the Rustrover MCP tool `build_project` to confirm the crate compiles:

```
mcp__rustrover__build_project(projectPath="<project-root>")
```

This catches cross-file issues that `get_file_problems` may miss (e.g., macro-expanded type errors).

## Checklist

- [ ] Ran `get_file_problems` on every edited `*.rs` file
- [ ] All reported errors are resolved (re-checked)
- [ ] Ran `reformat_file` on every edited file
- [ ] Ran `build_project` if the change touches public APIs, macros, or multiple crates
