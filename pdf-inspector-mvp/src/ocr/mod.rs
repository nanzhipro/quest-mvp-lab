//! OCR backends: a trait, a macOS Vision implementation, and a null fallback.

use crate::error::{PdfError, Result};
use crate::types::OcrText;

#[cfg(target_os = "macos")]
mod vision;

/// A backend that recognizes text inside raster images.
pub trait OcrBackend: Send + Sync {
    /// Backend identifier for reports.
    fn name(&self) -> &'static str;
    /// Recognize text in image bytes (JPEG or PNG).
    ///
    /// `format` is one of "jpeg", "png", "raw".
    fn recognize(&self, format: &str, data: &[u8]) -> Result<Vec<OcrText>>;
}

/// No-op backend for platforms without a native OCR engine.
pub struct NullOcr;

impl OcrBackend for NullOcr {
    fn name(&self) -> &'static str {
        "null"
    }

    fn recognize(&self, _format: &str, _data: &[u8]) -> Result<Vec<OcrText>> {
        Err(PdfError::Ocr(
            "no OCR backend available on this platform (Vision requires macOS)".into(),
        ))
    }
}

/// The default backend for the host platform.
pub fn default_backend() -> Box<dyn OcrBackend> {
    #[cfg(target_os = "macos")]
    {
        Box::new(vision::VisionOcr::new())
    }
    #[cfg(not(target_os = "macos"))]
    {
        Box::new(NullOcr)
    }
}

/// Normalize OCR text for comparison: collapse whitespace, keep case.
pub fn normalize(text: &str) -> String {
    text.split_whitespace().collect::<Vec<_>>().join(" ")
}

/// Case-insensitive substring check on normalized text.
pub fn contains_normalized(haystack: &str, needle: &str) -> bool {
    let hay = normalize(haystack).to_lowercase();
    let needle = normalize(needle).to_lowercase();
    hay.contains(&needle)
}
