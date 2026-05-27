# ADR 0009 — Sandbox is the isolation boundary for both commands and whole subagents

- **Status:** Accepted
- **Date:** 2026-05-25
- **Deciders:** Lamark core team
- **Supersedes:** the implicit "Environment runs commands only" model in plan/05 prior to 2026-05-25.
- **Related:** [plan/05c](../../plan/05c-sandbox-and-agent-hosting.md), [plan/05a](../../plan/05a-coordinator-multi-agent.md), [plan/13](../../plan/13-remote-ui.md), [ADR-0008](./0008-local-env-no-host-sandbox.md).

## Context

The pre-2026-05-25 plan named its isolation trait `Environment` and gave it a command-shaped surface (`exec`, `read_file`, `write_file`, `list_files`, `workspace_root`, `lifecycle_close`). Subagents were spawned by the coordinator (plan/05a) by constructing fresh `Session` objects directly. That meant:

1. Two parallel isolation stories — one for commands (`Environment`), one for subagents (ad-hoc, in the coordinator).
2. No defined wire protocol for driving a subagent inside a container; the coordinator would need to reinvent it.
3. Confusion with codex's `linux-sandbox` / `windows-sandbox-rs` crates and with the `Sandbox` provider slot already defined in plan/00c §162.
4. No host-side budget enforcement for subagents — only the child's own self-limit.
5. The "7 environment backends" claim in plan/00b was aspirational; only Local + Docker + SSH had any real design.

We also have the hermes-agent precedent (`tools/environments/*` is where subagents run too) and codex's multi-agent v2 protocol (`spawn_agent`, `followup_task`, `send_message`, `close_agent`) — both treat the isolation surface as the place that hosts the child.

## Decision

- Rename `Environment` → **`Sandbox`** (one name across plan/05, plan/05c, plan/00c, plan/05a, SPEC).
- Extend the trait with an **`spawn_agent(spec) -> AgentHandle`** primitive whose return value exposes the same `Submission` / `Event` enums as an in-process `Session`.
- The coordinator (plan/05a) **only** spawns subagents through `sandbox.spawn_agent(spec)`. It no longer constructs `Session` directly.
- Ship **three** in-tree backends at v0.1: `LocalSandbox` (in-process + forked-process modes), `DockerSandbox` (default for untrusted prompts), `SshSandbox`. The other four (`ModalSandbox`, `DaytonaSandbox`, `SingularitySandbox`, `VercelSandbox`) become plugin candidates via the `Sandbox` provider slot.
- Reuse the **`lamark.v1`** proto from plan/13 (Remote UI) as the SQ/EQ wire format across sandbox boundaries (Docker unix socket, SSH-tunneled socket, future Modal stream). One protocol; multiple transports.
- The in-sandbox entrypoint is a new CLI subcommand: **`lamark agent run --spec PATH [--events PATH | --stdio]`**.
- Egress policy is first-class on the trait: `None | ModelProviderOnly | Allowlist(...)`. Default for `DockerSandbox` is `ModelProviderOnly`.
- Tool surface in the child is selectable: `Inherit | Restricted | ProxyToParent`. Default for `DockerSandbox` is `Restricted` (= inherit, filtered by allowlist).
- Budget is enforced **twice** — child-side via its own `Session` config, parent-side via `AgentHandle::wait()` deadline + `interrupt()` + `kill()`.

## Consequences

### Positive

- One isolation story. Tool execution and subagent hosting share the same boundary, trait, and wire protocol.
- The coordinator gets smaller — it builds `AgentSpec`s and calls one method.
- Trace-bundle pull-back, network egress filtering, and budget enforcement are uniform across backends.
- Plugins (Modal, Daytona, Singularity, Vercel) implement *one* trait and inherit the agent-hosting protocol for free.
- Remote UI (plan/13) and sandbox agent hosting share `lamark.v1` proto. Wire-format changes are versioned in one place.

### Negative

- `lamark-envs` crate is renamed `lamark-sandbox`. Any external code referencing it needs to update — minor cost given pre-1.0 status.
- The trait surface is bigger. We accept that cost because the cohesion gain is larger than the surface-area cost.
- We commit to wire-protocol stability between the `lamark` binary that runs in the parent and the `lamark agent run` that runs in the child. Version-skew handling: child refuses to start if minor version drift > 1; documented operationally.

### Neutral

- Host-level sandboxing for `LocalSandbox` (landlock/seatbelt) remains deferred to v0.2 — ADR-0008 records that separately.

## Alternatives considered

1. **Keep `Environment` command-only; build a separate `AgentRunner` abstraction for subagents.**
   Rejected: two protocols, two trait surfaces, two failure modes. The coordinator would still need most of `Environment`'s capabilities (network policy, secrets, trace pull-back) reinvented.

2. **Run all subagents in-process; deny Docker-isolated subagents at v0.1.**
   Rejected: that's the codex / Claude-Code model and it's the right shape for trusted prompts only. Lamark's whole point is to be safe against prompt-injection-derived code execution; spawning untrusted prompts in-process violates that.

3. **Reuse `tonic` over TCP for sandbox-internal channels (instead of unix sockets).**
   Rejected: TCP requires port management, exposes traffic to local-network observers, and complicates container networking. Unix sockets bind-mounted into Docker are zero-config.

4. **Define `lamark.v2` proto specifically for sandbox internals; keep plan/13 on `lamark.v1`.**
   Rejected: keeping two protocols in lock-step is more work than one. The Sandbox transport is internal; the Remote UI transport is external; but they describe the same operations. Use one.

## Open follow-ups

- ADR-0010 (future): per-tool sandboxing — running only `Bash` in Docker while everything else stays in-process. Deferred to v0.2.
- ADR-0011 (future): GPU passthrough into `DockerSandbox` for vision/voice tools. Deferred to v0.2.
- ADR-0012 (future): supervisor model for hosting many concurrent sandboxes on one host (resource pooling, image cache, container reuse across sessions).
