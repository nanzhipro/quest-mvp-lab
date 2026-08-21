//! Wire protocol types for the OpenAI-compatible DeepSeek API.

use serde::{Deserialize, Serialize};

/// One content block inside a user message.
///
/// Serializes as `{"type": "text"|"image_url"|"file", ...}` — the exact shape
/// the vision API expects.
#[derive(Debug, Clone, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ContentBlock {
    /// Plain text.
    Text { text: String },
    /// Inline image via base64 data URL (optionally with a detail level).
    ImageUrl { image_url: ImageUrl },
    /// Image referenced by a Files API `file_id`.
    File { file_id: String },
}

impl ContentBlock {
    pub fn text(text: impl Into<String>) -> Self {
        ContentBlock::Text { text: text.into() }
    }

    pub fn image_url(url: impl Into<String>, detail: Option<String>) -> Self {
        ContentBlock::ImageUrl {
            image_url: ImageUrl {
                url: url.into(),
                detail,
            },
        }
    }

    pub fn file(file_id: impl Into<String>) -> Self {
        ContentBlock::File {
            file_id: file_id.into(),
        }
    }
}

/// The `image_url` sub-object with an optional detail level.
#[derive(Debug, Clone, Serialize)]
pub struct ImageUrl {
    pub url: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub detail: Option<String>,
}

/// A chat message: role + content blocks.
#[derive(Debug, Clone, Serialize)]
pub struct ChatMessage {
    pub role: String,
    pub content: Vec<ContentBlock>,
}

impl ChatMessage {
    pub fn user_text(text: impl Into<String>) -> Self {
        ChatMessage {
            role: "user".into(),
            content: vec![ContentBlock::text(text)],
        }
    }

    pub fn user(blocks: Vec<ContentBlock>) -> Self {
        ChatMessage {
            role: "user".into(),
            content: blocks,
        }
    }
}

/// Chat completion request body.
#[derive(Debug, Clone, Serialize)]
pub struct ChatRequest {
    pub model: String,
    pub messages: Vec<ChatMessage>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub max_tokens: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub temperature: Option<f32>,
    /// Thinking-mode control: `{"type": "enabled"|"disabled"}`.
    ///
    /// DeepSeek's thinking mode can consume the entire `max_tokens` budget on
    /// reasoning, leaving `content` empty — disable it for capability probes
    /// that need crisp, complete answers.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub thinking: Option<Thinking>,
}

/// DeepSeek thinking-mode switch.
#[derive(Debug, Clone, Serialize)]
pub struct Thinking {
    #[serde(rename = "type")]
    pub type_: String,
}

impl ChatRequest {
    pub fn new(model: &str, messages: Vec<ChatMessage>) -> Self {
        ChatRequest {
            model: model.to_string(),
            messages,
            max_tokens: None,
            temperature: None,
            thinking: None,
        }
    }

    pub fn with_max_tokens(mut self, max_tokens: u32) -> Self {
        self.max_tokens = Some(max_tokens);
        self
    }

    /// Disable thinking mode (deterministic, budget-friendly answers).
    pub fn with_thinking_disabled(mut self) -> Self {
        self.thinking = Some(Thinking {
            type_: "disabled".into(),
        });
        self
    }
}

/// Chat completion response.
#[derive(Debug, Clone, Deserialize)]
pub struct ChatResponse {
    pub id: String,
    pub object: String,
    pub created: i64,
    pub model: String,
    pub choices: Vec<Choice>,
    pub usage: Usage,
}

impl ChatResponse {
    /// Convenience accessor for the first choice's text content.
    pub fn text(&self) -> Option<&str> {
        self.choices
            .first()
            .and_then(|c| c.message.content.as_deref())
    }
}

#[derive(Debug, Clone, Deserialize)]
pub struct Choice {
    pub index: i64,
    pub message: ResponseMessage,
    #[serde(default)]
    pub finish_reason: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct ResponseMessage {
    pub role: String,
    #[serde(default)]
    pub content: Option<String>,
    #[serde(default)]
    pub reasoning_content: Option<String>,
}

/// Token usage reported by the API.
#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
pub struct Usage {
    pub prompt_tokens: u64,
    pub completion_tokens: u64,
    pub total_tokens: u64,
}

/// `{"error": {...}}` envelope returned on API errors.
#[derive(Debug, Clone, Deserialize)]
pub struct ApiErrorResponse {
    pub error: ApiError,
}

#[derive(Debug, Clone, Deserialize)]
pub struct ApiError {
    pub message: String,
    #[serde(rename = "type", default)]
    pub type_: String,
    #[serde(default)]
    pub code: Option<String>,
}

/// A file object returned by `POST /files`.
#[derive(Debug, Clone, Deserialize)]
pub struct FileObject {
    pub id: String,
    pub object: String,
    pub bytes: u64,
    pub created_at: i64,
    pub filename: String,
    pub purpose: String,
    #[serde(default)]
    pub expires_at: Option<i64>,
}
