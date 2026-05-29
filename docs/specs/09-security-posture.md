# 09 — Security posture

- **Default execution backend = `local` (dev tier); `kubernetes` is the recommended production tier.** Lamark trades container-by-default for a permission-first policy: every shell- and write-class tool gates on `Decision::Prompt` unless an explicit allowlist entry says otherwise. Hermes's failure mode was ALLOW-ALL on the policy layer (Issue #7826), not the absence of a container; Lamark fixes the policy layer (see [`../plan/05c-sandbox-and-agent-hosting.md`](../plan/05c-sandbox-and-agent-hosting.md) §"PermissionRequest", policy DSL in `agent/crates/lamark/policy.toml`) and leaves the sandbox choice to the operator.
  - **Try / dev:** `local` — fastest path, no infra. Permission-first policy is the safety layer.
  - **Single-host prod / air-gapped:** `docker` — container-per-session, egress allowlist via sidecar proxy.
  - **Multi-tenant production / horizontal scale:** `kubernetes` — Pod-per-subagent, NetworkPolicy egress, namespace-scoped RBAC, declarative resource quotas. See [`../plan/05d-sandbox-config-examples.md`](../plan/05d-sandbox-config-examples.md) for the production manifest.
  - **Build-server ops:** `ssh` — works for operator-managed hosts; cannot enforce egress (caveat).
  - Switch via `lamark config set sandbox.default <name>` or per-invocation `--sandbox <name>`.
- **Hook approval chain**: every shell-class / write-class tool fires `PermissionRequest`; default policy is `prompt`; declarative `Allow|Prompt|Forbidden` rules in `agent/crates/lamark/policy.toml`. See [`04-tooling-and-protocol.md`](./04-tooling-and-protocol.md) §4.
- **Trace redaction**: pre-storage hook can redact inline (opt-in); the dataset pipeline always re-runs Stage 1 + Stage 2 before any frontier-model curation.
- **Knowledge-base auth**: bearer token in `KB_TOKEN`; per-project ACL via knowledge-base's RBAC (it ships with auth/RBAC service — see its §4.1).
- **Consent**: `learning.consent_required=true` for any multi-user install; per-session `~/.lamark/no-collect` opt-out.
