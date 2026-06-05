//! OpenAI-compatible LLM client for Lamark.

use async_trait::async_trait;
use lamark_core::{LLMClient, LLMResponse, ToolCall, ToolDefinition, Conversation, Message};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone)]
pub struct OpenAIClient {
    pub base_url: String,
    pub model: String,
    pub api_key: String,
}

impl OpenAIClient {
    pub fn new(base_url: String, model: String, api_key: String) -> Self {
        Self { base_url, model, api_key }
    }
}

#[derive(Serialize)]
struct ChatRequest {
    model: String,
    messages: Vec<ChatMessage>,
    tools: Option<Vec<OpenAITool>>,
}

#[derive(Serialize)]
struct ChatMessage {
    role: String,
    content: String,
}

#[derive(Serialize)]
struct OpenAITool {
    #[serde(rename = "type")]
    type_: String,
    function: OpenAIFunctionDef,
}

#[derive(Serialize)]
struct OpenAIFunctionDef {
    name: String,
    description: String,
    parameters: serde_json::Value,
}

#[derive(Deserialize)]
struct ChatResponse {
    choices: Vec<Choice>,
}

#[derive(Deserialize)]
struct Choice {
    message: ChoiceMessage,
}

#[derive(Deserialize)]
struct ChoiceMessage {
    #[allow(dead_code)]
    role: String,
    content: Option<String>,
    tool_calls: Option<Vec<OpenAIToolCall>>,
}

#[derive(Deserialize)]
struct OpenAIToolCall {
    #[allow(dead_code)]
    id: String,
    function: OpenAIFunctionCall,
}

#[derive(Deserialize)]
struct OpenAIFunctionCall {
    name: String,
    arguments: String,
}

#[derive(Deserialize)]
#[allow(dead_code)]
struct ErrorResponse {
    error: Option<ErrorBody>,
}

#[derive(Deserialize)]
#[allow(dead_code)]
struct ErrorBody {
    message: Option<String>,
}

impl From<ToolDefinition> for OpenAITool {
    fn from(tool: ToolDefinition) -> Self {
        Self {
            type_: "function".to_string(),
            function: OpenAIFunctionDef {
                name: tool.name,
                description: tool.description,
                parameters: tool.parameters,
            },
        }
    }
}

#[async_trait]
impl LLMClient for OpenAIClient {
    async fn generate(
        &self,
        system_prompt: &str,
        conversation: &Conversation,
        tools: &[ToolDefinition],
    ) -> Result<LLMResponse, Box<dyn std::error::Error + Send + Sync>> {
        let mut messages = Vec::new();

        messages.push(ChatMessage {
            role: "system".to_string(),
            content: system_prompt.to_string(),
        });

        for msg in &conversation.messages {
            match msg {
                Message::User(text) => messages.push(ChatMessage {
                    role: "user".to_string(),
                    content: text.clone(),
                }),
                Message::Assistant(text) => messages.push(ChatMessage {
                    role: "assistant".to_string(),
                    content: text.clone(),
                }),
                Message::Tool(result) => messages.push(ChatMessage {
                    role: "tool".to_string(),
                    content: result.output.clone(),
                }),
                Message::System(text) => messages.push(ChatMessage {
                    role: "system".to_string(),
                    content: text.clone(),
                }),
            }
        }

        let tools_vec: Vec<OpenAITool> = tools.iter().cloned().map(From::from).collect();

        let client = reqwest::Client::new();
        let url = format!("{}/chat/completions", self.base_url);

        let response = client
            .post(url)
            .bearer_auth(&self.api_key)
            .json(&ChatRequest {
                model: self.model.clone(),
                messages,
                tools: if tools_vec.is_empty() { None } else { Some(tools_vec) },
            })
            .send()
            .await?;

        let body = response.text().await?;

        let chat_response: ChatResponse = serde_json::from_str(&body).map_err(|_| {
            if let Ok(err) = serde_json::from_str::<ErrorResponse>(&body) {
                err.error.and_then(|e| e.message).unwrap_or_else(|| "Unknown error from LLM provider".to_string())
            } else {
                body.clone()
            }
        })?;

        let choice = chat_response.choices.first().ok_or("No response from LLM")?;
        let message = &choice.message;

        let tool_calls = if let Some(calls) = &message.tool_calls {
            calls
                .iter()
                .map(|call| ToolCall {
                    name: call.function.name.clone(),
                    arguments: serde_json::from_str(&call.function.arguments).unwrap_or(serde_json::Value::Object(Default::default())),
                })
                .collect()
        } else {
            Vec::new()
        };

        Ok(LLMResponse {
            content: message.content.clone(),
            tool_calls,
        })
    }
}
