//! Figment-based layered configuration loader.

use std::path::{Path, PathBuf};
use std::sync::Arc;

use figment::{
    providers::{Env, Format, Yaml},
    Figment,
};

use crate::{error::ConfigError, types::Config};

/// Options forwarded from the CLI to the config loader.
#[derive(Debug, Default)]
pub struct LoadOptions {
    /// Override the config file path (`--config`).
    pub config_file: Option<PathBuf>,
    /// Active profile name (`--profile`).
    pub profile: Option<String>,
    /// Dotted-path `key=value` overrides from `-o` flags.
    pub overrides: Vec<String>,
}

/// Load configuration from all sources according to the precedence order.
///
/// Sources (lowest → highest):
/// 1. Compiled-in defaults
/// 2. `/etc/lamark/config.yaml`
/// 3. `~/.lamark/config.yaml`
/// 4. `./.lamark/config.yaml`
/// 5. Profile overlay `~/.lamark/profiles/<name>.yaml`
/// 6. `LAMARK__*` environment variables (double-underscore nesting separator)
/// 7. CLI `-o` flag overrides
///
/// # Errors
///
/// Returns [`ConfigError::Figment`] if any source fails to parse, or
/// [`ConfigError::Invalid`] if the merged result fails validation.
pub fn load(opts: &LoadOptions) -> Result<Arc<Config>, ConfigError> {
    let mut figment = Figment::from(figment::providers::Serialized::defaults(Config::default()));

    // System-wide config.
    figment = figment.merge(Yaml::file("/etc/lamark/config.yaml"));

    // User config (or explicit override).
    let user_cfg = opts
        .config_file
        .clone()
        .unwrap_or_else(|| expand_home("~/.lamark/config.yaml"));
    figment = figment.merge(Yaml::file(&user_cfg));

    // Project-local config.
    figment = figment.merge(Yaml::file(".lamark/config.yaml"));

    // Profile overlay.
    if let Some(ref profile) = opts.profile {
        let profile_path = expand_home(&format!("~/.lamark/profiles/{profile}.yaml"));
        figment = figment.merge(Yaml::file(&profile_path));
    }

    // Environment variables: LAMARK__MODEL__PROVIDER → model.provider
    figment = figment.merge(
        Env::prefixed("LAMARK__")
            .split("__")
            .lowercase(true),
    );

    // CLI -o overrides: "model.provider=ollama"
    for kv in &opts.overrides {
        if let Some((key, value)) = kv.split_once('=') {
            figment = figment.merge(figment::providers::Serialized::global(
                key,
                serde_json::Value::String(value.to_owned()),
            ));
        } else {
            tracing::warn!("ignoring malformed -o override (expected key=value): {kv}");
        }
    }

    let config: Config = figment.extract()?;
    super::validate(&config)?;
    Ok(Arc::new(config))
}

/// Load config, creating `~/.lamark/config.yaml` from defaults if absent.
///
/// # Errors
///
/// Returns an error if the default config cannot be written or if the
/// resulting merged configuration is invalid.
pub fn load_or_init() -> Result<Arc<Config>, ConfigError> {
    let user_cfg = expand_home("~/.lamark/config.yaml");
    if !user_cfg.exists() {
        init_default_config(&user_cfg)?;
    }
    load(&LoadOptions::default())
}

fn init_default_config(path: &Path) -> Result<(), ConfigError> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let yaml = serde_yaml::to_string(&Config::default())
        .map_err(|e| ConfigError::Invalid(e.to_string()))?;
    std::fs::write(path, yaml.as_bytes())?;
    tracing::info!("created default config at {}", path.display());
    Ok(())
}

/// Expand a leading `~/` to the user's home directory.
#[must_use]
pub fn expand_home(path: &str) -> PathBuf {
    if let Some(rest) = path.strip_prefix("~/") {
        if let Ok(home) = std::env::var("HOME") {
            return PathBuf::from(home).join(rest);
        }
    }
    PathBuf::from(path)
}
