//! Configuration error types.

use crate::types::Config;
use std::path::PathBuf;
use thiserror::Error;

/// Errors that can occur during config loading or validation.
#[derive(Debug, Error)]
pub enum ConfigError {
    #[error("config file not found: {0}")]
    FileNotFound(PathBuf),

    #[error("failed to parse config file: {0}")]
    ParseError(#[from] serde_yaml::Error),

    #[error("missing required field: {0}")]
    MissingField(String),

    #[error("profile '{0}' not found in config")]
    ProfileNotFound(String),

    #[error("override parsing failed: {0} (expected format 'key.value=value')")]
    OverrideError(#[from] OverrideError),
}

/// Error parsing a single dot-notation override string.
#[derive(Debug, Error)]
pub enum OverrideError {
    #[error("expected format 'key.value=value', got '{0}'")]
    Malformed(String),
}

/// Apply a list of dot-notation overrides to a mutable Config.
pub fn apply_overrides(cfg: &mut Config, overrides: &[String]) -> Result<(), ConfigError> {
    for raw in overrides {
        let (key_path, value) = raw
            .split_once('=')
            .ok_or_else(|| ConfigError::OverrideError(OverrideError::Malformed(raw.clone())))?;

        let parts: Vec<&str> = key_path.split('.').collect();
        apply_single_override(cfg, &parts, value)?;
    }
    Ok(())
}

fn apply_single_override(cfg: &mut Config, parts: &[&str], value: &str) -> Result<(), ConfigError> {
    match parts {
        ["model", "provider"] => cfg.model.provider = value.to_owned(),
        ["model", "model_id"] => cfg.model.model_id = value.to_owned(),
        ["model", "api_key"] => cfg.model.api_key = Some(value.to_owned()),
        ["model", "base_url"] => cfg.model.base_url = Some(value.to_owned()),
        ["model", "reasoning"] => cfg.model.reasoning = Some(value.to_owned()),
        ["agent", "max_tokens"] => {
            cfg.agent.max_tokens =
                Some(value.parse().map_err(|_| {
                    ConfigError::MissingField(format!("invalid max_tokens: '{value}'"))
                })?)
        }
        other => {
            return Err(ConfigError::OverrideError(OverrideError::Malformed(
                other.join("."),
            )));
        }
    }
    Ok(())
}
