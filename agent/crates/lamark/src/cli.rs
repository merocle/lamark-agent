use clap::Parser;

/// Global options available to every subcommand.
#[derive(Debug, Parser)]
#[command(name = "lamark")]
#[command(version, about, long_about = None)]
pub struct GlobalOptions {
    /// Override config file (`--config /path/to/file.yaml`).
    #[arg(long, env = "LAMARK_CONFIG", global = true)]
    pub config: Option<PathBuf>,

    /// Active profile name (`--profile dev`).
    #[arg(long, env = "LAMARK_PROFILE", global = true)]
    pub profile: Option<String>,

    /// Override config keys (`-o model.provider=ollama`).
    #[arg(short = 'o', long, global = true, action = ArgAction::Append)]
    pub overrides: Vec<String>,

    /// Verbose logging (`-v` for info, `-vv` for debug, `-vvv` for trace).
    #[arg(short, long, action = ArgAction::Count, global = true)]
    pub verbose: u8,
}

/// The set of subcommands supported by `lamark`.
#[derive(Debug, Parser)]
#[command(subcommand)]
pub enum Cmd {
    /// Interactive chat or one‑shot query.
    Chat(ChatArgs),

    /// Run the long‑running HTTP gateway.
    Gateway(GatewayArgs),

    /// Expose Lamark as an MCP server.
    Mcp(McpArgs),

    /// Manage ACPs (agent communication protocol).
    Acp(AcpArgs),

    /// List bundled or installed skills.
    Skills(SkillsArgs),

    /// List or manage plugins.
    Plugins(PluginsArgs),

    /// Work with trace bundles.
    Trace(TraceArgs),

    /// Config management commands.
    Config(ConfigArgs),

    /// Gateway management commands.
    Webui(WebuiArgs),

    /// Remote control (gRPC/WS) commands.
    Remote(RemoteArgs),

    /// Run a sub‑agent or custom prompt.
    Agent(AgentArgs),

    /// Execute a batch manifest.
    Exec(ExecArgs),

    /// Doctor check (validate config, reachability, etc.).
    Doctor,

    /// Print version information.
    Version,
}

// ───── subcommand shapes (only Chat is fully detailed in the plan)

#[derive(Debug, Parser)]
#[command(name = "chat")]
pub struct ChatArgs {
    /// Prompt to execute (if omitted, enter interactive TUI).
    #[arg(value_name = "PROMPT...", num_args = "*")]
    pub prompt: Option<Vec<String>>,
}

#[derive(Debug, Parser)]
#[command(name = "gateway")]
pub struct GatewayArgs {}

#[derive(Debug, Parser)]
#[command(name = "mcp")]
pub struct McpArgs {}

#[derive(Debug, Parser)]
#[command(name = "acp")]
pub struct AcpArgs {}

#[derive(Debug, Parser)]
#[command(name = "skills")]
pub struct SkillsArgs {}

#[derive(Debug, Parser)]
#[command(name = "plugins")]
pub struct PluginsArgs {}

#[derive(Debug, Parser)]
#[command(name = "trace")]
pub struct TraceArgs {}

#[derive(Debug, Parser)]
#[command(name = "config")]
pub struct ConfigArgs {
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
    /// gRPC/WS endpoint to connect to.
    #[arg(long)]
    pub remote: Option<String>,
}

#[derive(Debug, Parser)]
#[command(name = "agent")]
pub struct AgentArgs {
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
    /// Path to a batch manifest (JSON/TOML).
    #[arg(long, value_name = "MANIFEST")]
    pub manifest: Option<PathBuf>,

    /// Concurrency level for batch execution.
    #[arg(long, default_value_t = 4)]
    pub concurrency: usize,
}

#[derive(Debug, Parser)]
#[command(name = "doctor")]
pub struct DoctorArgs {}
