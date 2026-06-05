//! Configuration loader for Lamark.

use anyhow::{Context, Result};
use serde_yaml;
use std::fs;
use std::path::{Path, PathBuf};

use crate::error::{apply_overrides, ConfigError};
use crate::types::{Config, LoadOptions};

/// Expands `~` to the user's home directory.
pub fn expand_home(path: PathBuf) -> PathBuf {
    let path_str = path.to_str().unwrap_or("");
    if path_str.starts_with('~') {
        if let Some(home) = std::env::var("HOME").ok() {
            return PathBuf::from(path_str.replacen('~', &home, 1));
        }
    }
    path
}

/// Loads configuration from a specific file and profile.
pub fn load(options: LoadOptions) -> Result<Config> {
    let config_path = options
        .resolve_config_path()
        .map(expand_home)
        .ok_or_else(|| ConfigError::FileNotFound(PathBuf::from("no config file provided")))?;

    let content = fs::read_to_string(&config_path)
        .with_context(|| format!("failed to read config file: {:?}", config_path))?;

    // In a real implementation, we would parse a YAML structure that supports profiles.
    // For now, we'll implement a simplified version that looks for the selected profile.
    let mut config =
        parse_config_with_profile(&content, options.profile.as_deref().unwrap_or("default"))?;

    if !options.overrides.is_empty() {
        apply_overrides(&mut config, &options.overrides)?;
    }

    Ok(config)
}

/// Loads configuration from default locations or initializes a default config if none exists.
pub fn load_or_init() -> Result<Config> {
    let default_path = PathBuf::from("config.yaml");
    if !default_path.exists() {
        init_default_config(&default_path)?;
    }

    load(LoadOptions {
        config_file: Some(default_path),
        ..Default::default()
    })
}

fn init_default_config(path: &Path) -> Result<()> {
    let default_yaml = r#"
profile: default
model:
  provider: openai
  model_id: mlx-community/Qwen3.6-35B-A3B-8bit
  api_key: no-api-key-needed
  base_url: http://127.0.0.1:52415/v1
agent:
  identity: "You are Lamark, a helpful AI agent."
  safety: AskFirst
  max_tokens: 4096
  task_completion_guidance: true
  tool_use_enforcement: auto
plugins: []
skill_paths: ["~/.lamark/skills"]
project_dir: "."
"#;
    fs::write(path, default_yaml).context("failed to write default config")?;
    Ok(())
}

fn parse_config_with_profile(content: &str, _profile: &str) -> Result<Config> {
    // Simplified parsing: assume the YAML is just the Config struct for now.
    // Real implementation would handle profile-based sections.
    let config: Config = serde_yaml::from_str(content).map_err(|e| ConfigError::ParseError(e))?;
    // In a real scenario, we'd check if 'profile' matches the config's profile.
    Ok(config)
}
