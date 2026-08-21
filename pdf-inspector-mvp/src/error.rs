//! Error type for the whole pipeline.

use thiserror::Error;

/// Errors produced by the pdf-inspector-mvp pipeline.
#[derive(Debug, Error)]
pub enum PdfError {
    /// The PDF could not be parsed or an object is malformed.
    #[error("PDF parse error: {0}")]
    Parse(String),
    /// Filesystem error.
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),
    /// The OCR backend could not run (unsupported platform, API failure).
    #[error("OCR error: {0}")]
    Ocr(String),
    /// Input validation failed.
    #[error("invalid input: {0}")]
    Invalid(String),
}

/// Convenience result alias for the whole crate.
pub type Result<T> = std::result::Result<T, PdfError>;
