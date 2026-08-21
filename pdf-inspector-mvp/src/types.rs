//! Shared data types for the extraction pipeline.

use serde::{Deserialize, Serialize};

/// PDF type classification; mirrors `firecrawl/pdf-inspector`'s detector.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum PdfType {
    /// Extractable text layer (Tj/TJ operators) on most pages.
    TextBased,
    /// Image-only pages — the document is a scan.
    Scanned,
    /// Mostly images with little or no text.
    ImageBased,
    /// Mix of text-heavy and image-heavy pages.
    Mixed,
}

impl PdfType {
    /// Stable machine-readable name (matches upstream's string values).
    pub fn as_str(self) -> &'static str {
        match self {
            PdfType::TextBased => "text_based",
            PdfType::Scanned => "scanned",
            PdfType::ImageBased => "image_based",
            PdfType::Mixed => "mixed",
        }
    }
}

/// A run of text with its position on the page.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TextItem {
    /// The decoded text of the run.
    pub text: String,
    /// Left edge, PDF coordinates (origin bottom-left).
    pub x: f32,
    /// Baseline, PDF coordinates (origin bottom-left).
    pub y: f32,
    /// Advance width in page points.
    pub width: f32,
    /// Approximate height (font size scaled by CTM).
    pub height: f32,
    /// BaseFont name of the font used.
    pub font: String,
    /// Nominal font size (before CTM scaling).
    pub size: f32,
    /// 1-indexed PDF page number.
    pub page: u32,
}

/// A positioned line of text — the reading-order unit.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PdfLine {
    /// Joined text of the line (spaces synthesized from horizontal gaps).
    pub text: String,
    /// Left edge in page points.
    pub x0: f32,
    /// Right edge in page points.
    pub x1: f32,
    /// Baseline in page points.
    pub y: f32,
    /// Largest font size on the line.
    pub size: f32,
    /// Font of the first item on the line.
    pub font: String,
    /// 1-indexed PDF page number.
    pub page: u32,
    /// True when the majority of the line uses a monospaced font.
    pub monospace: bool,
}

/// An embedded image found on a page.
#[derive(Debug, Clone)]
pub struct ImageItem {
    /// 1-indexed PDF page number.
    pub page: u32,
    /// Placement left in page points (bottom-left origin).
    pub x: f32,
    /// Placement bottom in page points (bottom-left origin).
    pub y: f32,
    /// Placement width in page points.
    pub w: f32,
    /// Placement height in page points.
    pub h: f32,
    /// Object number in the PDF.
    pub xref: u32,
    /// Container format: "jpeg", "png", or "raw".
    pub format: String,
    /// Pixel width.
    pub width: u32,
    /// Pixel height.
    pub height: u32,
    /// Image bytes in `format` (for "raw": decoded pixels, RGB).
    pub data: Vec<u8>,
}

impl ImageItem {
    /// Format string usable in a Markdown image link.
    pub fn ext(&self) -> &'static str {
        match self.format.as_str() {
            "jpeg" => "jpg",
            "png" => "png",
            _ => "img",
        }
    }
}

/// OCR result for one image.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OcrText {
    /// Recognized string.
    pub text: String,
    /// Normalized bounding box left (0..1, bottom-left origin).
    pub x: f32,
    /// Normalized bounding box bottom (0..1, bottom-left origin).
    pub y: f32,
    /// Normalized bounding box width (0..1).
    pub w: f32,
    /// Normalized bounding box height (0..1).
    pub h: f32,
}

/// An image drawn by a `Do` operator: resource name + placement rectangle.
#[derive(Debug, Clone)]
pub struct ImagePlacement {
    /// XObject resource name.
    pub name: Vec<u8>,
    /// Placement left in page points (bottom-left origin).
    pub x: f32,
    /// Placement bottom in page points (bottom-left origin).
    pub y: f32,
    /// Placement width in page points.
    pub w: f32,
    /// Placement height in page points.
    pub h: f32,
}

/// Per-page extraction output.
#[derive(Debug, Clone, Default)]
pub struct PageExtraction {
    /// 1-indexed PDF page number.
    pub page: u32,
    /// Positioned text runs, in content-stream order.
    pub items: Vec<TextItem>,
    /// Embedded images (data resolved).
    pub images: Vec<ImageItem>,
    /// Image placements from `Do` operators (resolved into `images`).
    pub placements: Vec<ImagePlacement>,
    /// Text-show operator count (Tj/TJ/'/") — detector signal.
    pub text_op_count: u32,
    /// Image `Do` operator count — detector signal.
    pub image_op_count: u32,
}

/// Per-page summary for reports.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PageSummary {
    /// 1-indexed PDF page number.
    pub pdf_page: u32,
    /// Printed page number inferred from the running head, if any.
    pub printed_page: Option<u32>,
    /// Characters of text extracted.
    pub text_chars: usize,
    /// Embedded image count.
    pub images: usize,
    /// Text-show operator count.
    pub text_ops: u32,
}

/// Full document report.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PdfReport {
    /// Document classification.
    pub pdf_type: PdfType,
    /// Classification confidence 0..1.
    pub confidence: f32,
    /// Total page count.
    pub page_count: u32,
    /// Pages with a usable text layer.
    pub pages_with_text: u32,
    /// Pages containing at least one embedded image.
    pub pages_with_images: u32,
    /// True when any page has images (image OCR may matter).
    pub ocr_recommended: bool,
    /// 1-indexed pages whose text layer is too thin for extraction.
    pub pages_needing_ocr: Vec<u32>,
    /// Per-page summaries.
    pub pages: Vec<PageSummary>,
    /// Markdown conversion, if requested.
    pub markdown: Option<String>,
}
