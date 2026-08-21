//! Configuration: API-key-safe loading from environment variables.

use std::fmt;

/// Runtime configuration for the DeepSeek API.
///
/// The API key is private and never appears in [`Debug`]/[`Display`] output,
/// error messages, or serialized reports.
#[derive(Clone)]
pub struct Config {
    api_key: String,
    model: String,
    base_url: String,
}

impl Config {
    /// Build a config from explicit values (used by tests and mocks).
    pub fn new(api_key: String, model: String, base_url: String) -> Self {
        Config {
            api_key,
            model,
            base_url,
        }
    }

    /// Load from environment variables (`DEEPSEEK_API_KEY`, `DS_MODEL`, `DS_BASE_URL`).
    ///
    /// `DS_MODEL` and `DS_BASE_URL` fall back to the vision model and the
    /// official endpoint. The caller is responsible for loading `.env` first
    /// (see `main`); this function only reads the process environment so tests
    /// stay hermetic.
    pub fn from_env() -> anyhow::Result<Self> {
        let api_key = std::env::var("DEEPSEEK_API_KEY").map_err(|_| {
            anyhow::anyhow!("环境变量 DEEPSEEK_API_KEY 未设置：请检查 .env 文件或导出该变量")
        })?;
        if api_key.trim().is_empty() {
            anyhow::bail!("环境变量 DEEPSEEK_API_KEY 为空");
        }
        let model = std::env::var("DS_MODEL")
            .unwrap_or_else(|_| "deepseek-v4-flash-vision-exp".to_string());
        let base_url =
            std::env::var("DS_BASE_URL").unwrap_or_else(|_| "https://api.deepseek.com".to_string());
        Ok(Config {
            api_key,
            model,
            base_url,
        })
    }

    /// The API key. Callers must never log or serialize it.
    pub fn api_key(&self) -> &str {
        &self.api_key
    }

    /// Model name, defaults to the vision model.
    pub fn model(&self) -> &str {
        &self.model
    }

    /// API base URL, defaults to the official endpoint.
    pub fn base_url(&self) -> &str {
        &self.base_url
    }

    /// Redact the API key from an arbitrary message (defense in depth).
    pub fn redact(&self, msg: &str) -> String {
        msg.replace(self.api_key(), "[REDACTED]")
    }
}

impl fmt::Debug for Config {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Config")
            .field("api_key", &"[REDACTED]")
            .field("model", &self.model)
            .field("base_url", &self.base_url)
            .finish()
    }
}

impl fmt::Display for Config {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "Config {{ api_key: [REDACTED], model: {}, base_url: {} }}",
            self.model, self.base_url
        )
    }
}
