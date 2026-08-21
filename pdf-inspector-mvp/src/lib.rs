//! Minimal Rust re-implementation of Firecrawl's `pdf-inspector`: PDF
//! classification, positioned text extraction, reading-order layout, Markdown
//! conversion, and OCR of embedded images via the macOS Vision framework.

#![warn(missing_docs)]

pub mod detector;
pub mod error;
pub mod extractor;
pub mod layout;
pub mod markdown;
pub mod ocr;
pub mod page_map;
pub mod types;
pub mod verify;

use std::path::Path;

use detector::{Detection, DetectionConfig};
use lopdf::{Document, Object};
use markdown::{MarkdownOptions, PageContent};
use types::PageSummary;
pub use types::{ImageItem, OcrText, PdfLine, PdfReport, PdfType, TextItem};

pub use error::{PdfError, Result};

/// Pipeline options for `process_pdf*`.
#[derive(Debug, Clone)]
pub struct PdfOptions {
    /// Detection configuration.
    pub detection: DetectionConfig,
    /// Convert the extracted text to Markdown.
    pub to_markdown: bool,
    /// Emit `---` page separators in the Markdown output.
    pub page_breaks: bool,
}

impl Default for PdfOptions {
    fn default() -> Self {
        Self {
            detection: DetectionConfig::default(),
            to_markdown: true,
            page_breaks: true,
        }
    }
}

/// Run the full pipeline (detect + extract + markdown) on a PDF file.
pub fn process_pdf<P: AsRef<Path>>(path: P) -> Result<PdfReport> {
    process_pdf_with_options(path, &PdfOptions::default())
}

/// Run the full pipeline with custom options.
pub fn process_pdf_with_options<P: AsRef<Path>>(
    path: P,
    options: &PdfOptions,
) -> Result<PdfReport> {
    let doc = Document::load(path.as_ref()).map_err(|e| PdfError::Parse(e.to_string()))?;
    process_document(&doc, options)
}

/// Run the pipeline on an already-loaded document.
pub fn process_document(doc: &Document, options: &PdfOptions) -> Result<PdfReport> {
    let detection = detector::detect_document_with_config(doc, &options.detection)?;
    let pages = extractor::extract_pages(doc)?;
    let page_h = page_height(doc).unwrap_or(792.0);
    let total = pages.len();
    let printed = printed_pages(&pages, page_h);

    let lines = layout::group_into_lines(&extractor::all_items(&pages));
    let ordered = layout::reading_order(lines);
    let stripped = layout::strip_running_headers(ordered, page_h, total);

    let markdown = if options.to_markdown {
        let mut contents = Vec::with_capacity(total);
        for p in &pages {
            let page_lines: Vec<types::PdfLine> = stripped
                .iter()
                .filter(|l| l.page == p.page)
                .cloned()
                .collect();
            contents.push(PageContent {
                lines: page_lines,
                images: &p.images,
                printed_page: printed[p.page as usize],
                page_h,
            });
        }
        Some(markdown::to_markdown(
            &contents,
            &MarkdownOptions {
                include_page_breaks: options.page_breaks,
            },
        ))
    } else {
        None
    };

    let summaries: Vec<PageSummary> = pages
        .iter()
        .map(|p| PageSummary {
            pdf_page: p.page,
            printed_page: printed[p.page as usize],
            text_chars: p.items.iter().map(|i| i.text.chars().count()).sum(),
            images: p.images.len(),
            text_ops: p.text_op_count,
        })
        .collect();

    Ok(PdfReport {
        pdf_type: detection.pdf_type,
        confidence: detection.confidence,
        page_count: detection.page_count,
        pages_with_text: detection.pages_with_text,
        pages_with_images: detection.pages_with_images,
        ocr_recommended: detection.ocr_recommended,
        pages_needing_ocr: detection.pages_needing_ocr.clone(),
        pages: summaries,
        markdown,
    })
}

/// Best-effort page height from the first page's MediaBox.
pub fn page_height(doc: &Document) -> Option<f32> {
    let pages = doc.get_pages();
    let (_, first) = pages.iter().next()?;
    let dict = doc.get_dictionary(*first).ok()?;
    let box_obj = dict.get(b"MediaBox").ok()?;
    let arr = box_obj.as_array().ok()?;
    if arr.len() < 4 {
        return None;
    }
    let y0 = match &arr[1] {
        Object::Integer(i) => *i as f32,
        Object::Real(f) => *f,
        _ => 0.0,
    };
    let y1 = match &arr[3] {
        Object::Integer(i) => *i as f32,
        Object::Real(f) => *f,
        _ => 0.0,
    };
    Some((y1 - y0).abs().max(1.0))
}

/// Printed page number per PDF page index (1-indexed), from running heads.
pub fn printed_pages(pages: &[extractor::PageExtraction], page_h: f32) -> Vec<Option<u32>> {
    let mut out = vec![None; pages.len() + 1];
    let lines = layout::group_into_lines(&extractor::all_items(pages));
    let mut by_page: Vec<Vec<types::PdfLine>> = vec![Vec::new(); pages.len()];
    for l in lines {
        by_page[(l.page - 1) as usize].push(l);
    }
    for (i, page_lines) in by_page.iter().enumerate() {
        out[i + 1] = page_map::printed_page_number(page_lines, page_h);
    }
    out
}

/// Convenience: classify a document without full extraction.
pub fn detect<P: AsRef<Path>>(path: P) -> Result<Detection> {
    let doc = Document::load(path.as_ref()).map_err(|e| PdfError::Parse(e.to_string()))?;
    detector::detect_document(&doc)
}

/// Convenience: extract positioned text items for a file.
pub fn extract_text_items<P: AsRef<Path>>(path: P) -> Result<Vec<TextItem>> {
    let doc = Document::load(path.as_ref()).map_err(|e| PdfError::Parse(e.to_string()))?;
    Ok(extractor::all_items(&extractor::extract_pages(&doc)?))
}
