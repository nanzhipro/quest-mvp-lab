//! Document classification: TextBased / Scanned / ImageBased / Mixed.

use lopdf::{Document, ObjectId};
use serde::Serialize;

use crate::error::Result;
use crate::extractor::fonts::page_content;
use crate::types::PdfType;

/// Detection configuration (mirrors upstream's `DetectionConfig`).
#[derive(Debug, Clone)]
pub struct DetectionConfig {
    /// Maximum pages to scan; 0 = all pages.
    pub max_pages: u32,
    /// Minimum text-show operators for a page to count as text-bearing.
    pub min_text_ops_per_page: u32,
    /// Ratio of text-bearing pages above which the doc is TextBased.
    pub text_page_ratio: f32,
}

impl Default for DetectionConfig {
    fn default() -> Self {
        Self {
            max_pages: 0,
            min_text_ops_per_page: 3,
            text_page_ratio: 0.6,
        }
    }
}

/// Per-page detection signals.
#[derive(Debug, Clone, Serialize)]
pub struct PageSignal {
    /// 1-indexed page number.
    pub page: u32,
    /// Count of text-show operators (Tj/TJ/'/").
    pub text_ops: u32,
    /// Count of image `Do` operators.
    pub image_ops: u32,
    /// True when the page has a usable text layer.
    pub has_text: bool,
}

/// Document-level detection result.
#[derive(Debug, Clone, Serialize)]
pub struct Detection {
    /// Document classification.
    pub pdf_type: PdfType,
    /// Confidence 0..1.
    pub confidence: f32,
    /// Total page count.
    pub page_count: u32,
    /// Pages with a usable text layer.
    pub pages_with_text: u32,
    /// Pages containing at least one image.
    pub pages_with_images: u32,
    /// True when any page contains images (image OCR may matter).
    pub ocr_recommended: bool,
    /// 1-indexed pages whose text layer is too thin.
    pub pages_needing_ocr: Vec<u32>,
    /// Per-page signals.
    pub signals: Vec<PageSignal>,
}

/// Count text/image operators by scanning content streams.
pub fn scan_signals(doc: &Document) -> Result<Vec<PageSignal>> {
    let pages = doc.get_pages();
    let mut signals = Vec::with_capacity(pages.len());
    for (num, id) in pages {
        signals.push(scan_page(doc, id, num)?);
    }
    signals.sort_by_key(|s| s.page);
    Ok(signals)
}

/// Classify a document from its page signals.
pub fn classify(signals: &[PageSignal], config: &DetectionConfig) -> Detection {
    let total = signals.len().max(1) as u32;
    let text_pages = signals.iter().filter(|s| s.has_text).count() as u32;
    let image_pages = signals.iter().filter(|s| s.image_ops > 0).count() as u32;
    let ratio = text_pages as f32 / total as f32;

    let pdf_type = if ratio >= config.text_page_ratio {
        PdfType::TextBased
    } else if image_pages == total {
        PdfType::Scanned
    } else if text_pages > 0 {
        PdfType::Mixed
    } else {
        PdfType::ImageBased
    };

    // Confidence: distance from the classification boundary, clamped.
    let target = if ratio >= config.text_page_ratio {
        config.text_page_ratio
    } else {
        0.0
    };
    let confidence = (1.0 - (ratio - target).abs() * 1.25).clamp(0.2, 1.0);

    Detection {
        pdf_type,
        confidence,
        page_count: total,
        pages_with_text: text_pages,
        pages_with_images: image_pages,
        ocr_recommended: image_pages > 0,
        pages_needing_ocr: signals
            .iter()
            .filter(|s| !s.has_text)
            .map(|s| s.page)
            .collect(),
        signals: signals.to_vec(),
    }
}

/// Classify a document end-to-end.
pub fn detect_document(doc: &Document) -> Result<Detection> {
    detect_document_with_config(doc, &DetectionConfig::default())
}

/// Classify with a custom configuration.
pub fn detect_document_with_config(doc: &Document, config: &DetectionConfig) -> Result<Detection> {
    let mut signals = scan_signals(doc)?;
    if config.max_pages > 0 && signals.len() as u32 > config.max_pages {
        // Evenly sample up to max_pages.
        let step = signals.len() as f32 / config.max_pages as f32;
        signals = (0..config.max_pages)
            .map(|i| signals[((i as f32 * step).floor() as usize).min(signals.len() - 1)].clone())
            .collect();
    }
    Ok(classify(&signals, config))
}

fn scan_page(doc: &Document, page_id: ObjectId, page_num: u32) -> Result<PageSignal> {
    let content = page_content(doc, page_id)?;
    let mut text_ops = 0u32;
    let mut image_ops = 0u32;
    if let Ok(ops) = lopdf::content::Content::decode(&content) {
        for op in &ops.operations {
            match op.operator.as_str() {
                "Tj" | "TJ" | "'" | "\"" => text_ops += 1,
                "Do" => image_ops += 1,
                _ => {}
            }
        }
    }
    Ok(PageSignal {
        page: page_num,
        text_ops,
        image_ops,
        has_text: text_ops >= 3,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sig(page: u32, text: u32, image: u32) -> PageSignal {
        PageSignal {
            page,
            text_ops: text,
            image_ops: image,
            has_text: text >= 3,
        }
    }

    #[test]
    fn text_based() {
        let signals = vec![sig(1, 10, 0), sig(2, 8, 0), sig(3, 12, 0)];
        let d = classify(&signals, &DetectionConfig::default());
        assert_eq!(d.pdf_type, PdfType::TextBased);
        assert!(!d.ocr_recommended);
        assert!(d.pages_needing_ocr.is_empty());
    }

    #[test]
    fn scanned() {
        let signals = vec![sig(1, 0, 1), sig(2, 0, 1), sig(3, 0, 1)];
        let d = classify(&signals, &DetectionConfig::default());
        assert_eq!(d.pdf_type, PdfType::Scanned);
        assert!(d.ocr_recommended);
        assert_eq!(d.pages_needing_ocr, vec![1, 2, 3]);
    }

    #[test]
    fn mixed() {
        // 2/3 pages carry text → TextBased overall, but the image-only page
        // still needs OCR routing.
        let signals = vec![sig(1, 10, 0), sig(2, 0, 1), sig(3, 12, 0)];
        let d = classify(&signals, &DetectionConfig::default());
        assert_eq!(d.pdf_type, PdfType::TextBased);
        assert_eq!(d.pages_needing_ocr, vec![2]);
        assert!(d.ocr_recommended);
    }
}
