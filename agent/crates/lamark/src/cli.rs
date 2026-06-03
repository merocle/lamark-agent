use std::path::PathBuf;

use clap::{ArgAction, Parser};

/// The set of subcommands supported by `lamark`.
#[derive(Debug, Parser)]
#[command(name = "lamark", version, about, long_about = None)]
pub enum Cmd {
    /// Interactive chat or one-shot query.
    #[command()]
    Chat(ChatArgs),

    /// Run the long-running HTTP gateway.
    #[command()]
    Gateway(GatewayArgs),

    /// Expose Lamark as an MCP server.
    #[command()]
    Mcp(McpArgs),

    /// Manage ACPs (agent communication protocol).
    #[command()]
    Acp(AcpArgs),

    /// List bundled or installed skills.
    #[command()]
    Skills(SkillsArgs),

    /// List or manage plugins.
    #[command()]
    Plugins(PluginsArgs),

    /// Work with trace bundles.
    #[command()]
    Trace(TraceArgs),

    /// Config management commands.
    #[command()]
    Config(ConfigArgs),

    /// Gateway management commands.
    #[command()]
    Webui(WebuiArgs),

    /// Remote control (gRPC/WS) commands.
    #[command()]
    Remote(RemoteArgs),

    /// Run a sub-agent or custom prompt.
    #[command()]
    Agent(AgentArgs),

    /// Execute a batch manifest.
    #[command()]
    Exec(ExecArgs),

    /// Doctor check (validate config, reachability, etc.).
    #[command()]
    Doctor,

    /// Print version information.
    #[command()]
    Version,
}

// ───── global options (flattened onto Cmd; must come after subcommand name)

#[derive(Debug, Default)]
pub struct GlobalOptions {
    pub config: Option<PathBuf>,
    pub profile: Option<String>,
    pub overrides: Vec<String>,
    pub verbose: u8,
}

// ───── subcommand shapes (only Chat is fully detailed in the plan)

#[derive(Debug, Parser)]
#[command(name = "chat")]
pub struct ChatArgs {
    /// Global config file override (`--config /path/to/file.yaml`).
    #[arg(long, env = "LAMARK_CONFIG")]
    pub config: Option<PathBuf>,

    /// Active profile name (`--profile dev`).
    #[arg(long, env = "LAMARK_PROFILE")]
    pub profile: Option<String>,

    /// Override config keys (`-o model.provider=ollama`).
    #[arg(short = 'o', action = ArgAction::Append)]
    pub overrides: Vec<String>,

    /// Verbose logging (`-v` for info, `-vv` for debug, `-vvv` for trace).
    #[arg(short, long, action = ArgAction::Count)]
    pub verbose: u8,

    /// Prompt to execute (if omitted, enter interactive TUI).
    #[arg(value_name = "PROMPT", num_args = 0..)]
    pub prompt: Option<Vec<String>>,
}

#[derive(Debug, Parser)]
#[command(name = "gateway")]
pub struct GatewayArgs {
    #[arg(long, env = "LAMARK_CONFIG")]
    pub config: Option<PathBuf>,

    #[arg(long, env = "LAMARK_PROFILE")]
    pub profile: Option<String>,

    #[arg(short = 'o', action = ArgAction::Append)]
    pub overrides: Vec<String>,

    #[arg(short, long, action = ArgAction::Count)]
    pub verbose: u8,
}

#[derive(Debug, Parser)]
#[command(name = "mcp")]
pub struct McpArgs {
    #[arg(long, env = "LAMARK_CONFIG")]
    pub config: Option<PathBuf>,

    #[arg(long, env = "LAMARK_PROFILE")]
    pub profile: Option<String>,

    #[arg(short = 'o', action = ArgAction::Append)]
    pub overrides: Vec<String>,

    #[arg(short, long, action = ArgAction::Count)]
    pub verbose: u8,
}

#[derive(Debug, Parser)]
#[command(name = "acp")]
pub struct AcpArgs {
    #[arg(long, env = "LAMARK_CONFIG")]
    pub config: Option<PathBuf>,

    #[arg(long, env = "LAMARK_PROFILE")]
    pub profile: Option<String>,

    #[arg(short = 'o', action = ArgAction::Append)]
    pub overrides: Vec<String>,

    #[arg(short, long, action = ArgAction::Count)]
    pub verbose: u8,
}

#[derive(Debug, Parser)]
#[command(name = "skills")]
pub struct SkillsArgs {
    #[arg(long, env = "LAMARK_CONFIG")]
    pub config: Option<PathBuf>,

    #[arg(long, env = "LAMARK_PROFILE")]
    pub profile: Option<String>,

    #[arg(short = 'o', action = ArgAction::Append)]
    pub overrides: Vec<String>,

    #[arg(short, long, action = ArgAction::Count)]
    pub verbose: u8,
}

#[derive(Debug, Parser)]
#[command(name = "plugins")]
pub struct PluginsArgs {
    #[arg(long, env = "LAMARK_CONFIG")]
    pub config: Option<PathBuf>,

    #[arg(long, env = "LAMARK_PROFILE")]
    pub profile: Option<String>,

    #[arg(short = 'o', action = ArgAction::Append)]
    pub overrides: Vec<String>,

    #[arg(short, long, action = ArgAction::Count)]
    pub verbose: u8,
}

#[derive(Debug, Parser)]
#[command(name = "trace")]
pub struct TraceArgs {
    #[arg(long, env = "LAMARK_CONFIG")]
    pub config: Option<PathBuf>,

    #[arg(long, env = "LAMARK_PROFILE")]
    pub profile: Option<String>,

    #[arg(short = 'o', action = ArgAction::Append)]
    pub overrides: Vec<String>,

    #[arg(short, long, action = ArgAction::Count)]
    pub verbose: u8,
}

#[derive(Debug, Parser)]
#[command(name = "config")]
pub struct ConfigArgs {
    #[arg(long, env = "LAMARK_CONFIG")]
    pub config: Option<PathBuf>,

    #[arg(long, env = "LAMARK_PROFILE")]
    pub profile: Option<String>,

    #[arg(short = 'o', action = ArgAction::Append)]
    pub overrides: Vec<String>,

    #[arg(short, long, action = ArgAction::Count)]
    pub verbose: u8,

    /// List all config values.
    #[arg(long)]
    pub show: Option<bool>,

    /// Show the resolved config file path.
    #[arg(long)]
    pub path: Option<bool>,

    /// Edit the config file with $EDITOR.
    #[arg(long)]
    pub edit: Option<bool>,

    /// Validate the config and exit.
    #[arg(long, alias = "doctor")]
    pub doctor: Option<bool>,
}

#[derive(Debug, Parser)]
#[command(name = "webui")]
pub struct WebuiArgs {
    #[arg(long, env = "LAMARK_CONFIG")]
    pub config: Option<PathBuf>,

    #[arg(long, env = "LAMARK_PROFILE")]
    pub profile: Option<String>,

    #[arg(short = 'o', action = ArgAction::Append)]
    pub overrides: Vec<String>,

    #[arg(short, long, action = ArgAction::Count)]
    pub verbose: u8,

    /// Bind address for HTTP server (`host:port`). Default: `127.0.0.1:5050`.
    #[arg(long, default_value = "127.0.0.1:5050")]
    pub bind: String,

    /// Open the browser automatically after startup.
    #[arg(long, default_value = "false")]
    pub open: bool,

    /// Show the server status after boot.
    #[arg(long, default_value = "false")]
    pub status: bool,
}

#[derive(Debug, Parser)]
#[command(name = "remote")]
pub struct RemoteArgs {
    #[arg(long, env = "LAMARK_CONFIG")]
    pub config: Option<PathBuf>,

    #[arg(long, env = "LAMARK_PROFILE")]
    pub profile: Option<String>,

    #[arg(short = 'o', action = ArgAction::Append)]
    pub overrides: Vec<String>,

    #[arg(short, long, action = ArgAction::Count)]
    pub verbose: u8,

    /// gRPC/WS endpoint to connect to.
    #[arg(long)]
    pub remote: Option<String>,
}

#[derive(Debug, Parser)]
#[command(name = "agent")]
pub struct AgentArgs {
    #[arg(long, env = "LAMARK_CONFIG")]
    pub config: Option<PathBuf>,

    #[arg(long, env = "LAMARK_PROFILE")]
    pub profile: Option<String>,

    #[arg(short = 'o', action = ArgAction::Append)]
    pub overrides: Vec<String>,

    #[arg(short, long, action = ArgAction::Count)]
    pub verbose: u8,

    /// Path to the agent spec (YAML/JSON defining role, prompt, etc.).
    #[arg(long, value_name = "PATH")]
    pub spec: Option<PathBuf>,

    /// Stream tool output (default true for interactive).
    #[arg(long, default_value = "true")]
    pub events: Option<bool>,
}

#[derive(Debug, Parser)]
#[command(name = "exec")]
pub struct ExecArgs {
    #[arg(long, env = "LAMARK_CONFIG")]
    pub config: Option<PathBuf>,

    #[arg(long, env = "LAMARK_PROFILE")]
    pub profile: Option<String>,

    #[arg(short = 'o', action = ArgAction::Append)]
    pub overrides: Vec<String>,

    #[arg(short, long, action = ArgAction::Count)]
    pub verbose: u8,

    /// Path to a batch manifest (JSON/TOML).
    #[arg(long, value_name = "MANIFEST")]
    pub manifest: Option<PathBuf>,

    /// Concurrency level for batch execution.
    #[arg(long, default_value_t = 4)]
    pub concurrency: usize,
}

#[derive(Debug, Parser)]
#[command(name = "doctor")]
pub struct DoctorArgs {
    #[arg(long, env = "LAMARK_CONFIG")]
    pub config: Option<PathBuf>,

    #[arg(long, env = "LAMARK_PROFILE")]
    pub profile: Option<String>,

    #[arg(short = 'o', action = ArgAction::Append)]
    pub overrides: Vec<String>,

    #[arg(short, long, action = ArgAction::Count)]
    pub verbose: u8,
}
