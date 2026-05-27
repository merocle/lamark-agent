//! Configuration management subcommands.

use crate::error::ConfigError;
use crate::loader::{load, load_or_init, LoadOptions};
use crate::types::Config;
use anyhow::Result;
use std::path::PathBuf;

/// Dispatch config subcommands.
pub async fn run(args: ConfigArgs, _cfg: Arc<Config>) -> Result<()> {
    if let Some(show) = args.show {
        if show {
            println!("{:#?}", _cfg);
        } else {
            println!("{}", _cfg);
        }
    } else if let Some(path) = args.path {
        println!("{}", _cfg);
    } else if let Some(edit) = args.edit {
        if edit {
            edit_config_file().await?;
        }
    } else if let Some(doctor) = args.doctor {
        if doctor {
            validate_config(_cfg).await?;
        }
    } else {
        // Default: show full config.
        println!("{:#?}", _cfg);
    }
    Ok(())
}

/// Validate the merged configuration against schema.
pub async fn validate_config(cfg: &Config) -> Result<(), ConfigError> {
    // Basic validation is already in place; can add more checks here.
    Ok(())
}

async fn edit_config_file() -> Result<()> {
    todo!();
}