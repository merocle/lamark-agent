//! Type-safe identifiers for sessions, turns, rollouts, and agents.

use serde::{Deserialize, Serialize};
use std::fmt;

macro_rules! newtype_id {
    ($name:ident, $doc:literal) => {
        #[doc = $doc]
        #[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
        pub struct $name(uuid::Uuid);

        impl $name {
            /// Generate a new random ID using UUID v7 (time-ordered).
            pub fn new() -> Self {
                Self(uuid::Uuid::now_v7())
            }

            /// Wrap an existing UUID.
            pub fn from_uuid(id: uuid::Uuid) -> Self {
                Self(id)
            }

            /// Return the inner UUID.
            pub fn as_uuid(&self) -> uuid::Uuid {
                self.0
            }
        }

        impl Default for $name {
            fn default() -> Self {
                Self::new()
            }
        }

        impl fmt::Display for $name {
            fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                write!(f, "{}", self.0)
            }
        }
    };
}

newtype_id!(
    SessionId,
    "Identifies a single agent session (one CLI invocation or gateway connection)."
);
newtype_id!(
    TurnId,
    "Identifies a single request/response turn within a session."
);
newtype_id!(
    RolloutId,
    "Identifies a trace bundle; used to correlate trace files with KB uploads."
);
newtype_id!(AgentId, "Persistent agent identity (survives restarts).");
newtype_id!(
    ThreadId,
    "Sub-thread within a rollout (for multi-agent / subagent traces)."
);
