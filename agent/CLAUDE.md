# Lamark Development Guide

## Development Principles

### Code Style & Quality
- **Simplicity First**: Prefer straightforward implementations over premature abstractions. Three similar lines are better than a complex helper.
- **Concise Documentation**: Avoid explaining *what* the code does (let identifiers do that). Only add comments to explain the *why* for non-obvious constraints or workarounds.
- **Secure by Default**: Prioritize writing safe, secure, and correct code, avoiding common vulnerabilities.
- **Error Handling**: Validate at system boundaries (user input, external APIs). Trust internal framework guarantees.

### Architecture
- **Core Logic**: Fundamental agent abstractions reside in `lamark-core`.
- **Configuration**: All settings and provider defaults are managed by `lamark-config`.
- **CLI & Commands**: Orchestration and CLI interface are implemented in `lamark`.
- **No Hardcoding**: Avoid hardcoded values in the main logic; delegate default resolution to the configuration crate.

### Rust Conventions
- Use `async-trait` for asynchronous trait methods.
- Use `anyhow` for high-level error handling in the CLI and command layers.
- Prefer `Arc` for sharing configuration and clients across asynchronous tasks.

## Operational Guide

### Running the Project
The project is a Rust workspace. To run the main CLI:
```bash
cargo run -p lamark -- [subcommand] [args]
```

### Common Subcommands
- `chat`: Interactive chat or one-shot query.
- `config`: Configuration management.
- `doctor`: System health and config validation.

### Git Workflow
- Create new commits rather than amending.
- Use descriptive commit messages focusing on the "why".
- Follow the established PR template for changes.
