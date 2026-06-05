//! Interactive chat and one-shot query command.

use std::sync::Arc;
use std::io::{self, Write};

use lamark_config::Config;
use lamark_core::{AIAgent, Conversation, PassthroughHarness};
use crate::openai_client::OpenAIClient;
use crate::tools::DefaultExecutor;

/// Run the interactive chat session.
pub async fn run(args: crate::cli::ChatArgs, cfg: Arc<Config>) {
    let client = Arc::new(OpenAIClient::new(
        cfg.model.base_url(),
        cfg.model.model_id.clone(),
        cfg.model.api_key(),
    ));
    let harness = Arc::new(PassthroughHarness);
    let executor = Arc::new(DefaultExecutor::new(cfg.clone()));
    let agent = AIAgent::new(client, harness, executor);

    let mut conversation = Conversation::default();

    if !args.prompt.is_empty() {
        let prompt = args.prompt.join(" ");
        println!("Sourcing response for: \"{}\"", prompt);

        match agent.run_turn(cfg.clone(), &mut conversation, prompt).await {
            Ok(response) => println!("\nAgent: {}", response),
            Err(e) => eprintln!("Error: {}", e),
        }
        return;
    }

    println!("Entering interactive chat (type 'exit' or 'quit' to leave)...");
    loop {
        print!("> ");
        io::stdout().flush().unwrap();

        let mut input = String::new();
        if io::stdin().read_line(&mut input).is_err() {
            eprintln!("Failed to read input");
            break;
        }

        let trimmed = input.trim();
        if trimmed.is_empty() || trimmed == "exit" || trimmed == "quit" {
            break;
        }

        match agent.run_turn(cfg.clone(), &mut conversation, trimmed.to_string()).await {
            Ok(response) => println!("\nAgent: {}", response),
            Err(e) => eprintln!("Error: {}", e),
        }
    }
}

/// Stub: chat with explicit load options.
#[allow(dead_code)]
pub async fn run_with(_args: crate::cli::ChatArgs, _opts: lamark_config::LoadOptions, _cfg: Arc<Config>) {
    println!("chat run_with — not yet implemented");
}
