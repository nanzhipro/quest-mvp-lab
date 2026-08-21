//! The self-check loop condition: prove that a path hidden inside a page
//! screenshot is invisible to the text layer and only recoverable via OCR.

use std::path::Path;

use serde::Serialize;

use crate::detector::{self, Detection};
use crate::extractor;
use crate::layout;
use crate::ocr::{contains_normalized, OcrBackend};
use crate::page_map;
use crate::types::PageExtraction;
use crate::{PdfError, Result};

/// Parameters for the verify subcommand / self-check.
#[derive(Debug, Clone)]
pub struct VerifyParams {
    /// Printed page number to inspect (default: 257).
    pub printed_page: u32,
    /// Expected path hidden inside the figure image.
    pub expected_path: String,
    /// Substrings the text layer must contain (figure caption).
    pub expected_text: Vec<String>,
    /// Substring that must NOT appear in the text layer.
    pub absent_text: String,
    /// Run the OCR stage (disable to demonstrate the loop failing).
    pub use_ocr: bool,
    /// Expected document page count (for the loop condition).
    pub expected_page_count: u32,
}

impl Default for VerifyParams {
    fn default() -> Self {
        Self {
            printed_page: 257,
            expected_path: "/Users/User/Library/LaunchAgents/com.apple.softwareupdate.plist".into(),
            expected_text: vec!["Figure 11-2".into(), "A BlockBlock alert".into()],
            absent_text: "com.apple.softwareupdate.plist".into(),
            use_ocr: true,
            expected_page_count: 26,
        }
    }
}

/// One self-check condition result.
#[derive(Debug, Clone, Serialize)]
pub struct Condition {
    /// Stable identifier.
    pub id: String,
    /// Human description.
    pub description: String,
    /// Pass/fail.
    pub pass: bool,
    /// Evidence for the report.
    pub detail: String,
}

/// Full verify report.
#[derive(Debug, Clone, Serialize)]
pub struct VerifyReport {
    /// Per-condition results.
    pub conditions: Vec<Condition>,
    /// True when every condition passed.
    pub all_pass: bool,
    /// Document-level detection result.
    pub detection: Detection,
    /// Text-layer OCR output found on the target page (evidence).
    pub ocr_text: Vec<String>,
}

/// Run the self-check loop condition against a PDF.
pub fn verify_loop_condition<P: AsRef<Path>>(
    pdf: P,
    params: &VerifyParams,
    ocr: &dyn OcrBackend,
) -> Result<VerifyReport> {
    let doc = lopdf::Document::load(pdf.as_ref())
        .map_err(|e| PdfError::Parse(format!("cannot load {}: {e}", pdf.as_ref().display())))?;
    verify_document(&doc, params, ocr)
}

/// Run the self-check against an already-loaded document.
pub fn verify_document(
    doc: &lopdf::Document,
    params: &VerifyParams,
    ocr: &dyn OcrBackend,
) -> Result<VerifyReport> {
    let mut conditions = Vec::new();

    // 1. Document parses with the expected page count.
    let page_count = doc.get_pages().len() as u32;
    conditions.push(Condition {
        id: "page_count".into(),
        description: format!(
            "document has {} pages (expected {})",
            page_count, params.expected_page_count
        ),
        pass: page_count == params.expected_page_count,
        detail: format!("page_count={page_count}"),
    });

    let detection = detector::detect_document(doc)?;
    let pages = extractor::extract_pages(doc)?;
    let page_h = crate::page_height(doc).unwrap_or(792.0);

    // Per-page printed page numbers (from running heads).
    let mut printed: Vec<Option<u32>> = vec![None; page_count as usize + 1];
    let lines = layout::group_into_lines(&extractor::all_items(&pages));
    let mut by_page: Vec<Vec<_>> = vec![Vec::new(); page_count as usize];
    for l in &lines {
        by_page[(l.page - 1) as usize].push(l.clone());
    }
    for (i, page_lines) in by_page.iter().enumerate() {
        printed[i + 1] = page_map::printed_page_number(page_lines, page_h);
    }

    // 2. Locate the target page.
    let target = printed
        .iter()
        .position(|p| *p == Some(params.printed_page))
        .map(|i| i as u32)
        .or_else(|| {
            pages
                .iter()
                .find(|p| {
                    let t = extractor::page_text(p);
                    params
                        .expected_text
                        .iter()
                        .all(|s| contains_normalized(&t, s))
                })
                .map(|p| p.page)
        });
    let Some(target) = target else {
        conditions.push(Condition {
            id: "target_page".into(),
            description: format!("printed page {} found", params.printed_page),
            pass: false,
            detail: "no page matched the printed number or the caption text".into(),
        });
        return Ok(VerifyReport {
            conditions,
            all_pass: false,
            detection,
            ocr_text: Vec::new(),
        });
    };
    conditions.push(Condition {
        id: "target_page".into(),
        description: format!(
            "printed page {} maps to PDF page {}",
            params.printed_page, target
        ),
        pass: true,
        detail: format!(
            "printed numbers: {}",
            printed
                .iter()
                .enumerate()
                .skip(1)
                .map(|(i, p)| format!(
                    "{i}={}",
                    p.map(|v| v.to_string()).unwrap_or_else(|| "-".into())
                ))
                .collect::<Vec<_>>()
                .join(", ")
        ),
    });

    let page_ext: &PageExtraction = &pages[(target - 1) as usize];
    let page_text_all = extractor::page_text(page_ext);

    // 3. Caption present in the text layer. Matching is token-based and
    // whitespace-insensitive because this book's figures use non-breaking
    // spaces whose glyphs decode to "!" ("Figure!11-2:!A BlockBlock alert").
    for expect in &params.expected_text {
        let hit = expect
            .split_whitespace()
            .all(|tok| contains_normalized(&page_text_all, tok));
        conditions.push(Condition {
            id: "text_present".into(),
            description: format!("text layer contains {expect:?}"),
            pass: hit,
            detail: if hit {
                "found".into()
            } else {
                format!(
                    "missing from {} chars of extracted text",
                    page_text_all.chars().count()
                )
            },
        });
    }

    // 4. The path must be absent from the text layer (the loop).
    let absent_ok = !page_text_all.contains(&params.absent_text);
    conditions.push(Condition {
        id: "text_absent".into(),
        description: format!(
            "text layer does NOT contain {abs} (fast path misses it)",
            abs = params.absent_text
        ),
        pass: absent_ok,
        detail: if absent_ok {
            "confirmed absent".into()
        } else {
            "the path IS in the text layer — OCR not needed, loop invalid".into()
        },
    });

    // 5. The page has an embedded image.
    let images = &page_ext.images;
    conditions.push(Condition {
        id: "has_image".into(),
        description: format!("page {} has at least one embedded image", target),
        pass: !images.is_empty(),
        detail: format!("{} image(s)", images.len()),
    });

    // 6. OCR of the image recovers the hidden path.
    let mut ocr_text: Vec<String> = Vec::new();
    if params.use_ocr && !images.is_empty() {
        for img in images {
            match ocr.recognize(&img.format, &img.data) {
                Ok(texts) => {
                    for t in texts {
                        ocr_text.push(t.text.clone());
                    }
                }
                Err(e) => ocr_text.push(format!("[ocr error: {e}]")),
            }
        }
    }
    let ocr_joined = ocr_text.join("\n");
    let ocr_hit = contains_normalized(&ocr_joined, &params.expected_path);
    conditions.push(Condition {
        id: "ocr_recovers_path".into(),
        description: format!("image OCR recovers {path}", path = params.expected_path),
        pass: ocr_hit,
        detail: if ocr_hit {
            "path found in OCR output".into()
        } else {
            format!(
                "not found in {} OCR line(s){}",
                ocr_text.len(),
                if params.use_ocr {
                    ""
                } else {
                    " (OCR disabled)"
                }
            )
        },
    });

    let all_pass = conditions.iter().all(|c| c.pass);
    Ok(VerifyReport {
        conditions,
        all_pass,
        detection,
        ocr_text,
    })
}
