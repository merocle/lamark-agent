# ADR 0008 — v0.1 ships `LocalSandbox` without host-level sandboxing

- **Status:** Accepted
- **Date:** 2026-05-25
- **Deciders:** Lamark core team
- **Related:** [plan/05c](../../plan/05c-sandbox-and-agent-hosting.md), [plan/01 §"What we copy from codex-rs"](../../plan/01-rust-strategy.md), [ADR-0009](./0009-sandbox-hosts-agents.md).

## Context

Codex ships two host-level sandboxing crates: `linux-sandbox` (landlock + seccomp) and `windows-sandbox-rs` (AppContainer). They wrap a child process in OS-level isolation so that even if the child does something malicious (e.g. tries to `rm -rf $HOME`), the kernel refuses.

Lamark's `LocalSandbox` is the only in-tree backend that runs on the user's host process directly. Today it is implemented as a plain `tokio::process::Command` — no landlock, no seccomp, no AppContainer.

We considered three options:

1. **Copy codex's `linux-sandbox` / `windows-sandbox-rs` verbatim at v0.1.** Pros: defense in depth from day 1. Cons: those crates are deep platform integrations; landing them correctly is weeks of work; we lose `DockerSandbox`'s schedule slip in the process.
2. **Write our own thin landlock + seatbelt wrapper at v0.1.** Same cost as option 1, less mature.
3. **Ship `LocalSandbox` naked with an opt-in flag; default users to `DockerSandbox` for untrusted work; defer host-level sandbox to v0.2.**

## Decision

Option 3.

- `LocalSandbox` does **not** apply landlock / seatbelt / AppContainer at v0.1.
- `LocalSandbox` is **not** the default sandbox for prompt-derived work. The default is `DockerSandbox`. The user must explicitly opt in by setting `sandbox.default = local` in config or passing `--unsafe-local` on the CLI.
- Documentation prominently states that `LocalSandbox` provides **process isolation only**: separate PID, separate stdout/stderr, kill-group on cancel, `PR_SET_PDEATHSIG` on Linux. It does **not** restrict filesystem reach, network egress, or syscalls beyond what the host user already cannot do.
- v0.2 will add a `host-sandbox` cargo feature implementing landlock on Linux and seatbelt on macOS, ported from codex (Apache-2.0). Tracking issue to be opened when v0.1 ships.

## Consequences

### Positive

- Ship date intact. `DockerSandbox` covers the untrusted-prompt threat model; that's the load-bearing case.
- We avoid coupling v0.1 to platform-specific syscall surfaces that are still moving (landlock ABI 4 stabilized recently; seatbelt is undocumented; AppContainer is Windows-only).
- The `Sandbox` trait already abstracts the host-sandbox decision; a v0.2 `host-sandbox` feature plugs in as another `LocalSandbox` mode without rewriting callers.

### Negative

- A user who sets `sandbox.default = local` and then takes prompt-injection-derived input gets no kernel-level protection. We document this in red.
- We will likely have to issue a security-bulletin update when v0.2 ships, and instruct existing v0.1 users to enable the feature.

### Mitigations in v0.1

- `LocalSandbox` requires `--unsafe-local` for non-trusted roles, and the runtime logs a `WARN` line on every `spawn_agent` that uses it.
- `lamark doctor` reports "host-level sandbox: NOT ACTIVE" and links to this ADR.
- The `Bash` tool's policy (plan/05 §"Approval flow") defaults to `Prompt` for any session whose sandbox is `LocalSandbox`, regardless of role.

## Alternatives considered

1. **Refuse to ship `LocalSandbox` at v0.1 (Docker-only).** Rejected: many dev-loop users have neither Docker installed nor want to incur its startup cost for every `lamark chat` invocation.
2. **Ship a Rust-native sandbox wrapper from scratch.** Rejected: scope. Codex's prior art is the right port target, but porting is a v0.2 task.
3. **Use `firejail` / `bwrap` as the sandbox under `LocalSandbox`.** Rejected: third-party process dependency; not universally installed; different surface on each distro. Worth revisiting in v0.2 if landlock proves insufficient.

## Open follow-ups

- v0.2 milestone: implement `host-sandbox` feature with landlock (Linux) + seatbelt (macOS). Track as a single issue with subtasks per platform.
- Document recommended `LocalSandbox` use cases vs. `DockerSandbox` use cases in `docs/sandbox.md` (user-facing) — to be written alongside v0.1 ship.
