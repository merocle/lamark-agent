//! Declarative Allow | Prompt | Forbidden DSL parser and evaluator.
//!
//! Every shell- and write-class tool fires a `PermissionRequest`; the policy
//! evaluator consults `policy.toml` to determine the `Decision`.
