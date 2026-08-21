//! HTTP client: chat completions + Files API upload.

use crate::config::Config;
use crate::protocol::{
    ApiErrorResponse, ChatMessage, ChatRequest, ChatResponse, ContentBlock, FileObject,
};
use std::path::Path;

/// How an image is provided to the model.
#[derive(Debug, Clone)]
pub enum ImageInput {
    /// Base64 inline via `data:` URL (simplest for local files).
    DataUrl { data_url: String },
    /// Reference to a previously uploaded Files API object.
    FileId { file_id: String },
}

/// Minimal DeepSeek API client (blocking).
///
/// Only the endpoints this MVP needs: `/chat/completions` and `/files`.
pub struct DeepSeekClient {
    http: reqwest::blocking::Client,
    config: Config,
}

impl DeepSeekClient {
    pub fn new(config: Config) -> Self {
        let http = reqwest::blocking::Client::builder()
            .connect_timeout(std::time::Duration::from_secs(30))
            .timeout(std::time::Duration::from_secs(300))
            .build()
            .expect("failed to build HTTP client");
        DeepSeekClient { http, config }
    }

    /// The model name for the next chat request (from config).
    pub fn model(&self) -> &str {
        self.config.model()
    }

    /// POST a chat completion request.
    pub fn chat(&self, req: &ChatRequest) -> anyhow::Result<ChatResponse> {
        let url = format!("{}/chat/completions", self.config.base_url());
        let resp = self
            .http
            .post(&url)
            .bearer_auth(self.config.api_key())
            .json(req)
            .send()
            .map_err(|e| anyhow::anyhow!("请求失败（网络错误）: {e}"))?;

        let status = resp.status();
        let body = resp.text().unwrap_or_default();
        if !status.is_success() {
            return self.map_api_error(status, &body);
        }
        serde_json::from_str(&body).map_err(|e| {
            anyhow::anyhow!(
                "解析响应失败 (HTTP {status}): {e}\nbody: {}",
                truncate(&body, 500)
            )
        })
    }

    /// Convenience: one text prompt + a list of images in one user message.
    ///
    /// `thinking_disabled` turns off DeepSeek's reasoning mode — the vision
    /// model can burn the whole `max_tokens` budget on reasoning and return an
    /// empty `content`, so probes and short-answer tasks pass `true`.
    pub fn chat_with_images(
        &self,
        prompt: &str,
        images: &[ImageInput],
        max_tokens: Option<u32>,
        thinking_disabled: bool,
    ) -> anyhow::Result<ChatResponse> {
        let mut blocks = vec![ContentBlock::text(prompt)];
        for img in images {
            match img {
                ImageInput::DataUrl { data_url } => {
                    blocks.push(ContentBlock::image_url(data_url.clone(), None));
                }
                ImageInput::FileId { file_id } => {
                    blocks.push(ContentBlock::file(file_id.clone()));
                }
            }
        }
        let mut req = ChatRequest::new(self.config.model(), vec![ChatMessage::user(blocks)]);
        if let Some(mt) = max_tokens {
            req = req.with_max_tokens(mt);
        }
        if thinking_disabled {
            req = req.with_thinking_disabled();
        }
        self.chat(&req)
    }

    /// Upload an image via the Files API, returning its `file_id` handle.
    pub fn upload_file(&self, path: &Path) -> anyhow::Result<FileObject> {
        let bytes = std::fs::read(path)
            .map_err(|e| anyhow::anyhow!("读取文件失败 {}: {e}", path.display()))?;
        let file_name = path
            .file_name()
            .map(|s| s.to_string_lossy().into_owned())
            .unwrap_or_else(|| "image".to_string());

        let part = reqwest::blocking::multipart::Part::bytes(bytes)
            .file_name(file_name.clone())
            .mime_str(mime_for(&file_name).unwrap_or("application/octet-stream"))?;
        let form = reqwest::blocking::multipart::Form::new()
            .part("file", part)
            .text("purpose", "user_data");

        let url = format!("{}/files", self.config.base_url());
        let resp = self
            .http
            .post(&url)
            .bearer_auth(self.config.api_key())
            .multipart(form)
            .send()
            .map_err(|e| anyhow::anyhow!("上传失败（网络错误）: {e}"))?;

        let status = resp.status();
        let body = resp.text().unwrap_or_default();
        if !status.is_success() {
            return self.map_api_error(status, &body);
        }
        serde_json::from_str(&body).map_err(|e| {
            anyhow::anyhow!(
                "解析上传响应失败 (HTTP {status}): {e}\nbody: {}",
                truncate(&body, 500)
            )
        })
    }

    /// Turn a non-2xx response into an error, surfacing the API message but
    /// never the API key. Generic so both `chat` and `upload_file` can use it.
    fn map_api_error<T>(&self, status: reqwest::StatusCode, body: &str) -> anyhow::Result<T> {
        let detail = serde_json::from_str::<ApiErrorResponse>(body)
            .map(|e| e.error.message)
            .unwrap_or_else(|_| truncate(body, 300).to_string());
        let msg = format!("API 错误 (HTTP {status}): {detail}");
        // 防御性脱敏：即使上游把 key 回显进错误体也不会泄漏
        Err(anyhow::anyhow!("{}", self.config.redact(&msg)))
    }
}

fn truncate(s: &str, max: usize) -> &str {
    if s.len() <= max {
        s
    } else {
        &s[..s.floor_char_boundary(max)]
    }
}

fn mime_for(file_name: &str) -> Option<&'static str> {
    let lower = file_name.to_ascii_lowercase();
    if lower.ends_with(".jpg") || lower.ends_with(".jpeg") {
        Some("image/jpeg")
    } else if lower.ends_with(".png") {
        Some("image/png")
    } else if lower.ends_with(".gif") {
        Some("image/gif")
    } else if lower.ends_with(".webp") {
        Some("image/webp")
    } else {
        None
    }
}
