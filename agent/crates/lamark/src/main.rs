use clap::Parser;
use std::sync::Arc;

mod cli;
mod command;
mod openai_client;
mod tools;

use cli::{Cmd, GlobalOptions};
use lamark_config::{LoadOptions, load, load_or_init};

use tracing_subscriber::{EnvFilter, fmt};

/// Extract global options from full arg list (before subcommand name).
fn extract_global(args: &[String]) -> GlobalOptions {
    let mut config: Option<std::path::PathBuf> = None;
    let mut profile: Option<String> = None;
    let mut overrides: Vec<String> = Vec::new();
    let mut verbose: u8 = 0;

    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--config" => {
                if let Some(v) = args.get(i + 1) {
                    config = Some(std::path::PathBuf::from(v));
                    i += 2;
                } else {
                    i += 1;
                }
            }
            val if val.starts_with("--config=") => {
                config = Some(std::path::PathBuf::from(
                    val.strip_prefix("--config=").unwrap(),
                ));
                i += 1;
            }
            "--profile" => {
                if let Some(v) = args.get(i + 1) {
                    profile = Some(v.clone());
                    i += 2;
                } else {
                    i += 1;
                }
            }
            val if val.starts_with("--profile=") => {
                profile = Some(val.strip_prefix("--profile=").unwrap().to_owned());
                i += 1;
            }
            "-o" | "--override" => {
                if let Some(v) = args.get(i + 1) {
                    overrides.push(v.clone());
                    i += 2;
                } else {
                    i += 1;
                }
            }
            val if val.starts_with("-o") => {
                overrides.push(val.strip_prefix("-o").unwrap().to_owned());
                i += 1;
            }
            "-v" => {
                verbose = verbose.saturating_add(1);
                i += 1;
            }
            "--verbose" => {
                verbose = verbose.saturating_add(1);
                i += 1;
            }
            _ => i += 1,
        }
    }

    GlobalOptions {
        config,
        profile,
        overrides,
        verbose,
    }
}

fn make_load_options(global: &GlobalOptions) -> LoadOptions {
    LoadOptions {
        config_file: global.config.clone(),
        profile: global.profile.clone(),
        overrides: global.overrides.clone(),
    }
}

#[tokio::main]
async fn main() {
    let args = std::env::args().collect::<Vec<_>>();

    // Extract global options (scan all args; binary path at index 0 won't match any flag).
    // Note: global options only work when placed AFTER the subcommand name, e.g. `lamark chat -v`.
    let global = extract_global(&args);

    // Initialize tracing subscriber.
    let filter = if global.verbose == 0 {
        EnvFilter::new("info")
    } else if global.verbose == 1 {
        EnvFilter::new("debug")
    } else {
        EnvFilter::new("trace")
    };
    fmt().with_env_filter(filter).init();

    // Load config using global options.
    let cfg = Arc::new(if global.config.is_none() {
        load_or_init().unwrap_or_else(|e| {
            eprintln!("config error: {e}");
            std::process::exit(1);
        })
    } else {
        load(make_load_options(&global)).unwrap_or_else(|e| {
            eprintln!("config error: {e}");
            std::process::exit(1);
        })
    });

    // Parse subcommand: skip args[0] (binary path) since we already provide "lamark" as the program name.
    let mut sub_args = vec!["lamark".to_string()];
    sub_args.extend(args.get(1..).unwrap_or(&args).iter().cloned());

    match Cmd::parse_from(&sub_args) {
        Cmd::Chat(chat) => {
            command::chat::run(chat, cfg).await;
        }
        Cmd::Gateway(gw) => {
            let gw_opts = make_load_options(&global);
            command::gateway::run_with(gw, gw_opts, cfg);
        }
        Cmd::Mcp(mcp) => {
            let mcp_opts = make_load_options(&global);
            command::mcp::run_with(mcp, mcp_opts, cfg);
        }
        Cmd::Acp(acp) => {
            let acp_opts = make_load_options(&global);
            command::acp::run_with(acp, acp_opts, cfg);
        }
        Cmd::Skills(skills) => {
            let skills_opts = make_load_options(&global);
            command::skills::run_with(skills, skills_opts, cfg);
        }
        Cmd::Plugins(plugins) => {
            let plugins_opts = make_load_options(&global);
            command::plugins::run_with(plugins, plugins_opts, cfg);
        }
        Cmd::Trace(trace) => {
            let trace_opts = make_load_options(&global);
            command::trace::run_with(trace, trace_opts, cfg);
        }
        Cmd::Config(config) => {
            let config_opts = make_load_options(&global);
            command::config::run_with(config, config_opts, cfg);
        }
        Cmd::Webui(webui) => {
            let webui_opts = make_load_options(&global);
            command::webui::run_with(webui, webui_opts, cfg);
        }
        Cmd::Remote(remote) => {
            let remote_opts = make_load_options(&global);
            command::remote::run_with(remote, remote_opts, cfg);
        }
        Cmd::Agent(agent) => {
            let agent_opts = make_load_options(&global);
            command::agent::run_with(agent, agent_opts, cfg);
        }
        Cmd::Exec(exec) => {
            let exec_opts = make_load_options(&global);
            command::exec::run_with(exec, exec_opts, cfg);
        }
        Cmd::Doctor => command::doctor::run(cfg),
        Cmd::Version => {
            println!("lamark");
        }
    }
}

