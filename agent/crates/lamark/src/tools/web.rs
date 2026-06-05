//! Web tool implementations: WebSearch, WebFetch.

use lamark_core::{ToolCall, ToolResult, TurnContext};

pub async fn search(call: &ToolCall, _ctx: &TurnContext<'_>) -> ToolResult {
    let query = call.arguments.get("query").and_then(|v| v.as_str());

    let Some(query) = query else {
        return ToolResult::failure("WebSearch requires 'query' argument.".to_string());
    };

    let limit = call
        .arguments
        .get("limit")
        .and_then(|v| v.as_u64())
        .unwrap_or(5) as usize;
    let client = reqwest::Client::new();

    // Default: use DuckDuckGo via a simple fetch approach
    let url = format!("https://html.duckduckgo.com/html/?q={}", urlencoding(query));

    match client.get(&url).send().await {
        Ok(response) => {
            let body = match response.text().await {
                Ok(b) => b,
                Err(e) => return ToolResult::failure(format!("WebSearch failed: {e}")),
            };

            // Parse HTML for results (basic extraction)
            let results = parse_ddg_results(&body, limit);

            if results.is_empty() {
                ToolResult::success(format!(
                    "WebSearch for \"{query}\" — no results returned (backend may be unavailable). Try a different query or configure web_search_api in config."
                ))
            } else {
                let output = results
                    .iter()
                    .enumerate()
                    .map(|(i, r)| format!("{}. {} — {}\n   {}", i + 1, r.title, r.url, r.snippet))
                    .collect::<Vec<_>>()
                    .join("\n\n");
                ToolResult::success(output)
            }
        }
        Err(e) => ToolResult::failure(format!("WebSearch failed: {e}")),
    }
}

pub async fn fetch(call: &ToolCall, _ctx: &TurnContext<'_>) -> ToolResult {
    let url = call.arguments.get("url").and_then(|v| v.as_str());

    let Some(url) = url else {
        return ToolResult::failure("WebFetch requires 'url' argument.".to_string());
    };

    let prompt = call.arguments.get("prompt").and_then(|v| v.as_str());
    let client = reqwest::Client::new();

    match client.get(url).send().await {
        Ok(response) => {
            let status = response.status();
            if !status.is_success() {
                return ToolResult::failure(format!("WebFetch failed: HTTP {status} for {url}"));
            }

            let body = match response.text().await {
                Ok(b) => b,
                Err(e) => return ToolResult::failure(format!("WebFetch failed: {e}")),
            };

            // Simple HTML-to-markdown conversion
            let markdown = html_to_markdown(&body, prompt);

            ToolResult::success(markdown)
        }
        Err(e) => ToolResult::failure(format!("WebFetch failed: {e}")),
    }
}

struct SearchResult {
    title: String,
    url: String,
    snippet: String,
}

fn parse_ddg_results(html: &str, limit: usize) -> Vec<SearchResult> {
    let mut results = Vec::new();

    // Extract result snippets from DuckDuckGo HTML (simplified parsing)
    for snippet_block in html.split("result__snippet") {
        // Try to extract URL from result__url or result__a
        let url_start = snippet_block
            .find("result__a")
            .or_else(|| snippet_block.find("result__url"));

        if let Some(start) = url_start {
            // Try to find the href in surrounding HTML
            if let Some(after) = snippet_block[start..].find("href=\"") {
                let href_start = start + after + 6; // skip past 'href="'
                if let Some(end) = snippet_block[href_start..].find('"') {
                    let url = &snippet_block[href_start..href_start + end];

                    // Extract snippet text (after result__snippet)
                    let snippet_end = snippet_block.find("</div").unwrap_or(snippet_block.len());
                    let snippet_text = &snippet_block[0..snippet_end]
                        .trim()
                        .replace("<a[^>]*>", "")
                        .replace("</a>", " ")
                        .replace("<[^>]+>", " ")
                        .split_whitespace()
                        .collect::<Vec<&str>>()
                        .join(" ");

                    // Extract title from nearby "result__title"
                    let title = if let Some(title_start) = snippet_block.find("result__title") {
                        let after_title = &snippet_block[title_start..];
                        if let Some(title_href) = after_title.find("<a href=\"") {
                            let h_start = title_href + 8; // skip '<a href="'
                            if let Some(h_end) = after_title[h_start..].find('"') {
                                after_title[h_start..h_start + h_end].to_string()
                            } else {
                                String::new()
                            }
                        } else {
                            String::new()
                        }
                    } else {
                        snippet_text[..50.min(snippet_text.len())].to_string()
                    };

                    results.push(SearchResult {
                        title,
                        url: url.to_string(),
                        snippet: snippet_text[..150.min(snippet_text.len())].to_string(),
                    });

                    if results.len() >= limit {
                        return results;
                    }
                }
            }
        }
    }

    results
}

fn html_to_markdown(html: &str, prompt: Option<&str>) -> String {
    // Simple HTML-to-markdown: strip most tags, preserve headings and links
    let mut md = html.to_string();

    // Convert headings
    md = md.replace("<h1>", "\n# ").replace("</h1>", "\n");
    md = md.replace("<h2>", "\n## ").replace("</h2>", "\n");
    md = md.replace("<h3>", "\n### ").replace("</h3>", "\n");
    md = md.replace("<h4>", "\n#### ").replace("</h4>", "\n");
    md = md.replace("<h5>", "\n##### ").replace("</h5>", "\n");
    md = md.replace("<h6>", "\n###### ").replace("</h6>", "\n");

    // Convert links: <a href="...">text</a> -> [text](...)
    let mut new_md = String::with_capacity(md.len());
    let mut chars = md.chars().peekable();
    while let Some(c) = chars.next() {
        if c == '<' && chars.peek() == Some(&'a') {
            // Check for href=
            let buf: String = chars.by_ref().take(20).collect();
            let href_pos = buf.find("href=");
            if let Some(pos) = href_pos {
                // Extract URL
                let after_eq = buf[pos + 5..].trim_start_matches('=').trim();
                if after_eq.starts_with('"') {
                    let url: String = after_eq[1..].chars().take_while(|&cc| cc != '"').collect();
                    // Skip to > and capture link text
                    let mut found_gt = false;
                    while let Some(ch) = chars.next() {
                        if ch == '>' {
                            found_gt = true;
                            break;
                        }
                    }
                    // Read until </a>
                    let mut link_text = String::new();
                    if found_gt {
                        loop {
                            match chars.next() {
                                Some('<') => {
                                    let rest: String = chars.by_ref().take(3).collect();
                                    if rest == "/a>" {
                                        break;
                                    }
                                    link_text.push('<');
                                    for ch in rest.chars() {
                                        link_text.push(ch);
                                    }
                                }
                                Some(ch) => link_text.push(ch),
                                None => break,
                            }
                        }
                    }
                    new_md.push_str(&format!("[{}]({})", link_text.trim(), url));
                } else {
                    new_md.push('<');
                    new_md.push_str(&buf);
                }
            } else {
                new_md.push('<');
                new_md.push_str(&buf);
            }
        } else {
            new_md.push(c);
        }
    }
    md = new_md;

    // Convert bold/italic
    md = md.replace("<strong>", "**").replace("</strong>", "**");
    md = md.replace("<b>", "**").replace("</b>", "**");
    md = md.replace("<em>", "*").replace("</em>", "*");
    md = md.replace("<i>", "*").replace("</i>", "*");

    // Convert paragraphs and breaks
    md = md.replace("<p>", "\n\n").replace("</p>", "\n");
    md = md.replace("<br>", "\n").replace("<br/>", "\n");

    // Remove remaining HTML tags (simple approach)
    let mut result = String::with_capacity(md.len());
    let mut in_tag = false;
    for c in md.chars() {
        match c {
            '<' => in_tag = true,
            '>' => in_tag = false,
            _ if !in_tag => result.push(c),
            _ => {}
        }
    }
    md = result;

    // Clean up whitespace
    let md = md.split_whitespace().collect::<Vec<&str>>().join(" ");

    if let Some(prompt) = prompt {
        format!("Content requested: {prompt}\n\n{md}",)
    } else {
        md
    }
}

// URL encoding helper (avoid dependency — simple encode)
fn urlencoding(s: &str) -> String {
    s.replace(' ', "%20")
        .replace('&', "%26")
        .replace('#', "%23")
        .replace('?', "%3F")
}

#[cfg(test)]
mod tests {
    use super::*;
    use lamark_core::{Conversation, ToolCall, TurnContext};
    use serde_json::json;
    use std::sync::Arc;

    fn test_ctx() -> TurnContext<'static> {
        let conversation = Box::leak(Box::new(Conversation::default()));
        let config = Arc::new(lamark_config::Config::default());
        TurnContext {
            conversation,
            config,
        }
    }

    #[tokio::test]
    async fn test_websearch_missing_query() {
        let call = ToolCall {
            name: "WebSearch".into(),
            arguments: json!({}),
        };
        let ctx = test_ctx();
        let result = search(&call, &ctx).await;
        assert!(!result.success);
    }

    #[tokio::test]
    async fn test_websearch_no_results() {
        let call = ToolCall {
            name: "WebSearch".into(),
            arguments: json!({"query": "xyznonexistent_query_12345"}),
        };
        let ctx = test_ctx();
        let _result = search(&call, &ctx).await;
        // May succeed with no results message or fail if backend is down
        // We just check it doesn't panic
    }

    #[tokio::test]
    async fn test_webfetch_missing_url() {
        let call = ToolCall {
            name: "WebFetch".into(),
            arguments: json!({}),
        };
        let ctx = test_ctx();
        let result = fetch(&call, &ctx).await;
        assert!(!result.success);
    }

    #[tokio::test]
    async fn test_webfetch_invalid_url() {
        let call = ToolCall {
            name: "WebFetch".into(),
            arguments: json!({"url": "not-a-url"}),
        };
        let ctx = test_ctx();
        let _result = fetch(&call, &ctx).await;
        // Should fail since "not-a-url" is not a valid URL
    }

    #[tokio::test]
    async fn test_parse_ddg_results_empty() {
        let results = parse_ddg_results("", 5);
        assert!(results.is_empty());
    }

    #[tokio::test]
    async fn test_html_to_markdown_basic() {
        let html = "<h1>Title</h1><p>Hello world</p>";
        let md = html_to_markdown(html, None);
        assert!(md.contains("# Title"));
        assert!(md.contains("Hello world"));
    }

    #[tokio::test]
    async fn test_html_to_markdown_with_prompt() {
        let html = "<p>content</p>";
        let md = html_to_markdown(html, Some("summarize this"));
        assert!(md.contains("Content requested: summarize this"));
    }

    #[tokio::test]
    async fn test_urlencoding_simple() {
        assert_eq!(urlencoding("hello world"), "hello%20world");
    }

    #[tokio::test]
    async fn test_urlencoding_special_chars() {
        let encoded = urlencoding("key value#1?test");
        assert!(encoded.contains("%20")); // space
        assert!(encoded.contains("%23")); // #
        assert!(encoded.contains("%3F")); // ?
    }
}
