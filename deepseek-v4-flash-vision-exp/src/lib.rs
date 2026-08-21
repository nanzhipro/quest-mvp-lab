//! Library root for `ds-vision`: DeepSeek vision-model validation toolchain.

pub mod client;
pub mod config;
pub mod image;
pub mod prompts;
pub mod protocol;
pub mod reporter;

pub use client::{DeepSeekClient, ImageInput};
pub use config::Config;
pub use image::{detect_format, file_to_data_url, to_data_url, ImageFormat};
pub use protocol::{ChatMessage, ChatRequest, ChatResponse, ContentBlock, FileObject, Usage};
pub use reporter::{CompareReport, CompareRow, Report, ReportWriter, TaskResult};
