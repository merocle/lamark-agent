# 07 — Configuration

Single YAML, `~/.lamark/config.yaml` (full file shown in
[`../plan/03-layer-2-config-bootstrap.md`](../plan/03-layer-2-config-bootstrap.md)).
Highlights:

```yaml
model:
  provider: vllm                    # vllm | ollama | llamacpp | lmstudio | sglang | anthropic | openai | bedrock
  base_url: http://localhost:8000/v1
  name: Qwen/Qwen3.6-35B-A3B
  context_length: 32768
  cache:
    strategy: auto                  # auto | cache_control | prefix_hash | off
    ttl: 1h                         # only used for cache_control providers

sandbox:                            # ../plan/05c; worked examples in ../plan/05d
  default: local                    # local | docker | ssh | kubernetes (in-tree)
                                    # modal | daytona | singularity | vercel (plugin candidates)
                                    # local = dev default (try / iterate)
                                    # kubernetes = recommended production default
                                    # safety lives in the policy layer, not the sandbox
  docker:
    egress: model-provider-only     # none | model-provider-only | allowlist
    workspace_mount: copy-on-write
  kubernetes:
    kubeconfig: ~                   # null → KUBECONFIG env → ~/.kube/config → in-cluster SA
    namespace: lamark-agents        # one namespace per project / tenant
    service_account: lamark-runner
    image: lamark/runner:0.1.0
    egress: model-provider-only     # enforced via NetworkPolicy
    workspace_mount: copy-on-write  # copy-on-write requires CSI snapshot+clone support
    pending_timeout: 60s            # Pod must reach Running within this window
    gpu_node_selector: {}           # e.g. { "nvidia.com/gpu.product": "H100" }
  agent_hosting:
    subagent_default: forked        # in-process | forked | docker | kubernetes
    trusted_role_default: in-process # summarizer / plan-compactor / etc.
  lifetime_seconds: 600

agent:
  tool_use_enforcement: true
  max_iterations: 40
  worktree: true
  default_policy: prompt            # allow | prompt | forbid

memory:
  external_provider: knowledge-base # null | knowledge-base | honcho | mem0 | hindsight
  knowledge_base:
    base_url: http://localhost:8080
    project_id: lamark-default
    auth_token_env: KB_TOKEN

skills:
  search_paths:
    - "./.lamark/skills"
    - "~/.lamark/skills"
  curator:
    enable: true
    interval_hours: 168

plugins:
  enable: true
  search_paths:
    - "~/.lamark/plugins"

gateway:
  enable: false
  adapters: []                      # ["telegram", "slack", "discord", "mcp_serve", "rest"]
  bind: "127.0.0.1:5050"

trace:
  enable: true
  root: "~/.lamark/traces"
  upload_to_kb: true

learning:
  enable_collection: true
  consent_required: false
```
