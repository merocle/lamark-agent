# 11 — Build, test, deploy

> Cargo workspace mechanics, CI, release engineering, packaging.
> Cross-cuts every layer. Read after 01 to get the build context right.

## Workspace structure (recap)

```
lemark-project/
├── Cargo.toml                    # workspace root
├── Cargo.lock                    # committed
├── crates/                       # see plan/01 §"Cargo workspace layout"
├── proto/                        # external-facing .proto only (gateway, ACP)
├── plan/                         # this dir
├── docs/                         # mdBook source
├── scripts/
├── justfile                      # task runner
├── deny.toml                     # cargo-deny config
├── rust-toolchain.toml           # pin MSRV
├── rustfmt.toml
├── .cargo/
│   └── config.toml               # workspace cargo config
├── .github/workflows/
│   ├── ci.yml
│   ├── release.yml
│   └── docs.yml
├── docker/
│   ├── Dockerfile.runtime
│   ├── Dockerfile.runner          # sandbox runner image
│   └── compose.dev.yaml
├── docs/specs/                   # deep spec (split from the old SPEC.md)
├── README.md
├── AGENTS.md                     # for any AI working on Lamark itself
├── LICENSE                       # MIT
└── CHANGELOG.md
```

## `Cargo.toml` (workspace root)

```toml
[workspace]
resolver = "2"
members = [
    "crates/lamark",
    "crates/lamark-config",
    "crates/lamark-core",
    "crates/lamark-providers",
    "crates/lamark-tools",
    "crates/lamark-sandbox",
    "crates/lamark-hooks",
    "crates/lamark-trace",
    "crates/lamark-prompt",
    "crates/lamark-cache",
    "crates/lamark-memory",
    "crates/lamark-kb-client",
    "crates/lamark-skills",
    "crates/lamark-plugins",
    "crates/lamark-gateway",
    "crates/lamark-mcp",
    "crates/lamark-acp",
    "crates/lamark-webui",
    "crates/lamark-remote",
    "crates/lamark-policy",
    "crates/lamark-coordinator",
    "crates/lamark-protocol",
    "crates/lamark-test-utils",
]

[workspace.package]
edition = "2024"
rust-version = "1.94.1"
license = "MIT"
authors = ["Lamark contributors"]
repository = "https://github.com/<org>/lamark"

[workspace.dependencies]
tokio       = { version = "1", features = ["full"] }
tokio-util  = { version = "0.7", features = ["full"] }
reqwest     = { version = "0.12", default-features = false, features = ["json","rustls-tls","stream"] }
serde       = { version = "1", features = ["derive"] }
serde_json  = "1"
serde_yaml  = "0.9"
anyhow      = "1"
thiserror   = "1"
tracing     = "0.1"
tracing-subscriber = { version = "0.3", features = ["env-filter","json"] }
tracing-appender = "0.2"
clap        = { version = "4", features = ["derive","env"] }
figment     = { version = "0.10", features = ["yaml","env","toml"] }
ratatui     = "0.27"
crossterm   = "0.28"
dashmap     = "6"
arc-swap    = "1"
parking_lot = "0.12"
futures     = "0.3"
async-trait = "0.1"
tokenizers  = "0.20"
rmcp        = "0.x"           # match codex
wasmtime    = { version = "27", features = ["component-model","async"] }
libloading  = "0.8"
rusqlite    = { version = "0.32", features = ["bundled","fts5"] }
notify      = "6"
url         = "2"
chrono      = { version = "0.4", features = ["serde"] }
uuid        = { version = "1", features = ["v7","serde"] }
xxhash-rust = { version = "0.8", features = ["xxh3"] }
eventsource-stream = "0.2"
wiremock    = "0.6"           # dev only
insta       = "1"
rstest      = "0.23"
proptest    = "1"

[profile.dev]
opt-level = 1                 # deps faster; first-party still debug-friendly

[profile.release]
lto = "thin"
codegen-units = 1
strip = "symbols"
panic = "abort"
```

## `rust-toolchain.toml`

```toml
[toolchain]
channel = "1.94.1"
components = ["rustfmt", "clippy"]
profile = "minimal"
```

## `.cargo/config.toml`

```toml
[build]
# sccache for shared incremental cache
rustc-wrapper = "sccache"

[target.x86_64-unknown-linux-gnu]
rustflags = ["-C", "link-arg=-fuse-ld=lld"]

[target.aarch64-apple-darwin]
rustflags = ["-C", "link-arg=-fuse-ld=lld"]

[net]
git-fetch-with-cli = true
```

## `deny.toml` (cargo-deny)

```toml
[licenses]
allow = ["MIT", "Apache-2.0", "Apache-2.0 WITH LLVM-exception", "BSD-2-Clause",
         "BSD-3-Clause", "ISC", "Unicode-DFS-2016", "Zlib", "CC0-1.0"]
unlicensed = "deny"
copyleft = "deny"           # blocks GPL/AGPL/LGPL into the binary
default = "deny"

[bans]
multiple-versions = "warn"
deny = [
    # ban anything we explicitly don't want
    { name = "openssl-sys" },   # we use rustls
]

[advisories]
db-path = "~/.cargo/advisory-db"
vulnerability = "deny"
unmaintained = "warn"
yanked = "deny"

[sources]
unknown-registry = "deny"
unknown-git = "deny"
allow-git = []
```

## `justfile`

```just
default:
    just --list

# ───── build
build:
    cargo build --workspace --release

build-dev:
    cargo build --workspace

# ───── test
test:
    cargo nextest run --workspace
test-fast:
    cargo nextest run --workspace -E "not test(slow)"
test-e2e:
    LAMARK_E2E=1 cargo nextest run --workspace -E "test(e2e_)"

# ───── lint
fmt:
    cargo fmt --all
lint:
    cargo clippy --all-targets --all-features -- -D warnings
deny:
    cargo deny check

# ───── docs
docs:
    mdbook build docs
docs-serve:
    mdbook serve docs --open

# ───── ops
proto-gen:
    cargo run -p lamark-protocol --bin generate

snapshot:
    cargo insta accept

# ───── deps
audit:
    cargo audit

update:
    cargo update --workspace

# ───── release
release-bin TARGET:
    cargo build --release --target {{TARGET}}
    strip target/{{TARGET}}/release/lamark
    tar czf lamark-{{TARGET}}.tar.gz -C target/{{TARGET}}/release lamark
```

## Per-crate `Cargo.toml` shape

Every crate inherits workspace lints + uses workspace deps:

```toml
[package]
name = "lamark-foo"
version = "0.1.0"
edition.workspace = true
rust-version.workspace = true
license.workspace = true
repository.workspace = true

[lints]
workspace = true

[dependencies]
lamark-core = { path = "../lamark-core" }
tokio = { workspace = true }
serde = { workspace = true }
serde_json = { workspace = true }
tracing = { workspace = true }
thiserror = { workspace = true }

[dev-dependencies]
insta = { workspace = true }
rstest = { workspace = true }
wiremock = { workspace = true }
```

## Workspace lints

```toml
# /Cargo.toml
[workspace.lints.rust]
unsafe_code = "deny"
missing_docs = "warn"
unused_must_use = "deny"

[workspace.lints.clippy]
pedantic = { level = "warn", priority = -1 }
nursery = { level = "warn", priority = -1 }
unwrap_used = "deny"
expect_used = "warn"
panic = "deny"                  # except in tests
todo = "warn"
unimplemented = "warn"
print_stdout = "deny"           # use tracing
print_stderr = "deny"
```

`unsafe_code = "deny"` is workspace-wide except for the plugin host crate (`lamark-plugins`) which opts back in via `#![allow(unsafe_code)]` at the top with rationale.

## CI (`.github/workflows/ci.yml`)

```yaml
name: ci
on:
  push:
    branches: [main]
  pull_request:

jobs:
  check:
    strategy:
      matrix:
        os: [ubuntu-22.04, macos-14]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@1.94.1
        with: { components: rustfmt, clippy }
      - uses: mozilla-actions/sccache-action@v0.0.5
      - run: cargo fmt --all --check
      - run: cargo clippy --workspace --all-targets --all-features -- -D warnings
      - run: cargo deny check
      - run: cargo nextest run --workspace
      - run: cargo build --release --workspace
      - run: ls -la target/release/lamark
      - run: |
          # binary size budget
          SIZE=$(stat -c%s target/release/lamark 2>/dev/null || stat -f%z target/release/lamark)
          test "$SIZE" -lt 30000000   # 30 MB

  proto-up-to-date:
    runs-on: ubuntu-22.04
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@1.94.1
      - run: cargo run -p lamark-protocol --bin generate
      - run: git diff --exit-code     # fails if proto-gen output drifted

  no-pyworker-refs:
    runs-on: ubuntu-22.04
    steps:
      - uses: actions/checkout@v4
      - run: |
          if grep -r --include='*.rs' --include='*.md' -nE 'pyworker|python_bridge|strangler' .; then
            echo "Leftover bridge references found"; exit 1
          fi
```

## Release engineering (`.github/workflows/release.yml`)

Tag-driven (`v0.*`). Build for:
- `x86_64-unknown-linux-gnu` (musl variant optional v0.2)
- `x86_64-apple-darwin`
- `aarch64-apple-darwin`
- `aarch64-unknown-linux-gnu`

Outputs:
- `lamark-<target>.tar.gz` with the stripped binary + `LICENSE` + `README.md` + `lamark.zsh-completion`.
- A `homebrew-bump` step that opens a PR against a tap.
- An `npm-bump` step deferred to v0.2.

Signing: cosign + GitHub OIDC. Provenance: SLSA L3 attestation.

## Docker

`docker/Dockerfile.runtime`:

```dockerfile
FROM rust:1.94.1-bookworm AS builder
WORKDIR /src
COPY . .
RUN cargo build --release --workspace

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates ripgrep git docker-cli && rm -rf /var/lib/apt/lists/*
COPY --from=builder /src/target/release/lamark /usr/local/bin/lamark
COPY docker/runtime-entrypoint.sh /
ENTRYPOINT ["/runtime-entrypoint.sh"]
CMD ["gateway", "run"]
```

`docker/Dockerfile.runner` — minimal "sandbox runner" image used by `DockerSandbox` (plan/05c) as the agent-hosting container:

```dockerfile
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    coreutils findutils grep sed gawk curl ca-certificates \
    python3 git ripgrep && rm -rf /var/lib/apt/lists/*
RUN useradd -m -u 1000 runner
USER runner
WORKDIR /workspace
```

Compose for dev:

```yaml
# docker/compose.dev.yaml
services:
  vllm:
    image: vllm/vllm-openai:latest
    deploy: { resources: { reservations: { devices: [{ capabilities: [gpu] }] } } }
    command: ["--model","Qwen/Qwen3-8B-Instruct","--enable-auto-tool-choice","--tool-call-parser","hermes"]
    ports: ["8000:8000"]

  knowledge-base:
    image: knowledge-base/server:dev
    ports: ["8080:8080"]
    environment:
      KB_DB_URL: postgres://kb:kb@postgres:5432/kb
    depends_on: [postgres]

  postgres:
    image: pgvector/pgvector:pg16
    environment: { POSTGRES_USER: kb, POSTGRES_PASSWORD: kb, POSTGRES_DB: kb }
    volumes: [pgdata:/var/lib/postgresql/data]

volumes: { pgdata: }
```

## Documentation site

`docs/` is an mdBook with sections:

- Getting started (install + first chat)
- Configuration
- Gateway setup (per adapter)
- Skills (authoring + Curator)
- Plugins (authoring WASM + dylib)
- Architecture (links into `plan/` files)
- Training pipeline (operator runbook)
- Self-improvement strategy
- Knowledge-base integration
- Troubleshooting
- API reference (generated from rustdoc)

`mdbook serve docs --open` for local. CI deploys to `gh-pages` on tag.

## Versioning

- **Pre-1.0**: every crate at `0.x.y`. SemVer relaxed; minor bumps may break.
- **1.0**: cut together. From then on:
  - Public `lamark` binary: SemVer.
  - Public `.proto` files (gateway/ACP): SemVer.
  - Public crate `lamark-kb-client`: SemVer (downstream KB integrators depend on it).
  - Internal crates: tracked together, no semver guarantee.

## Test strategy

| Tier | Tools | When |
|---|---|---|
| Unit | `cargo test` per crate | every commit |
| Snapshot | `insta` | every PR; review-required diffs |
| Property | `proptest` | every PR |
| Integration (in-process) | `tokio::test` + `wiremock` | every PR |
| E2E (real provider) | `cargo nextest --features e2e` | nightly cron; gated `LAMARK_E2E=1` |
| Soak | systemd-run a gateway for 24h with synthetic traffic | weekly |

## Test taxonomy

- `tests/unit_…` — crate-local.
- `tests/integration_…` — cross-crate, in-process.
- `tests/e2e_…` — needs Docker / running vLLM / running KB.
- `tests/soak_…` — long-running, multi-hour.

`cargo nextest` profiles map to these via `nextest.toml`.

## Local dev quickstart

```bash
# clone, build, test
git clone <repo>
cd lamark
just lint test build

# start stack
docker compose -f docker/compose.dev.yaml up -d
# wait for vllm + kb

# configure
cp examples/config.dev.yaml ~/.lamark/config.yaml
cp examples/policy.dev.toml ~/.lamark/policy.toml
echo "KB_TOKEN=dev-token" > ~/.lamark/.env

# go
cargo run --release --bin lamark -- chat "hello"
```

## Release checklist

For each `v0.x` release:

1. `just lint deny test build` all green.
2. `cargo deny check` — no new transitives flagged.
3. Snapshot diffs reviewed via `cargo insta review`.
4. Binary size: `< 30 MB` for release on each target.
5. Documentation site builds; nothing broken.
6. `CHANGELOG.md` updated (Keep-a-Changelog).
7. Tag `v0.x` → CI release workflow.
8. Verify SLSA provenance file lands in the GH release.
9. Smoke-test the released tarball on a clean macOS + Linux VM.
10. Announce in `docs/changelog.md` and post to KB events.

## Observability in production

- Prometheus scrape endpoint on `:9090/metrics`.
- Structured logs to `~/.lamark/logs/`; rotated daily; 14-day retention.
- Trace bundles in `~/.lamark/traces/`; rotated per `trace.rotate.*` config.
- KB receives lineage events for every adapter promotion + rollback.
- Optional Langfuse / W&B integration via the trainer (plan/10).

## Cutover gate (P0 done)

- ✅ Empty workspace with all 20+ crates building (each with a stub `lib.rs`).
- ✅ CI workflow green on macOS + Linux.
- ✅ `just lint test build` runs in < 5 min on a clean machine.
- ✅ `cargo deny check` passes — no GPL/AGPL transitives.
- ✅ Binary size budget enforced.
- ✅ Docker dev compose works end-to-end (vLLM + KB + a stub Lamark) on a dev box.
