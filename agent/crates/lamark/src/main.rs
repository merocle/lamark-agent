use anyhow::Result;
use clap::Parser;

mod cli;
mod command;
mod signal;

use cli::Cmd;
use cli::GlobalOptions;
use lamark_config::{load, load_or_init, LoadOptions};

use tracing_subscriber::{EnvFilter, fmt};

#[tokio::main]
async fn main() -> Result<()> {
    let cli = GlobalOptions::parse();

    // Initialize tracing subscriber. Verbosity maps: 0=off, 1=info, 2+=debug/trace.
    let filter = if cli.verbose == 0 {
        EnvFilter::new("info")
    } else if cli.verbose == 1 {
        EnvFilter::new("debug")
    } else {
        EnvFilter::new("trace")
    };
    fmt()
        .with_env_filter(filter)
        .init();

    // Ensure the config file exists before loading.
    let _config = if cli.config.is_none() {
        load_or_init()?
    } else {
        load(&LoadOptions {
            config_file: Some(cli.config.clone().unwrap()),
            profile: cli.profile.clone(),
            overrides: cli.overrides.clone(),
        })?
    };

    match Cmd::parse() {
        Cmd::Chat(args) => command::chat::run(args, _config).await,
        Cmd::Gateway(_) => command::gateway::run(_config).await,
        Cmd::Mcp(args) => command::mcp::run(args, _config).await,
        Cmd::Acp(_) => command::acp::run(args, _config).await,
        Cmd::Skills(args) => command::skills::run(args, _config).await,
        Cmd::Plugins(args) => command::plugins::run(args, _config).await,
        Cmd::Trace(args) => command::trace::run(args, _config).await,
        Cmd::Config(args) => command::config::run(args, _config).await,
        Cmd::Webui(args) => command::webui::run(args, _config).await,
        Cmd::Remote(args) => command::remote::run(args, _config).await,
        Cmd::Agent(args) => command::agent::run(args, _config).await,
        Cmd::Exec(args) => command::exec::run(args, _config).await,
        Cmd::Doctor(_) => command::doctor::run(_config).await,
        Cmd::Version => {
            println!("lamark");
        }
    }

    Ok(())
}