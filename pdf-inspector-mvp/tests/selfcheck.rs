//! The self-check loop condition against the real test PDF.
//!
//! Skips (with a message) when the PDF is not present — the check is only
//! meaningful with the actual document.

use std::path::Path;

use pdf_inspector_mvp::ocr::{default_backend, NullOcr};
use pdf_inspector_mvp::verify::{verify_loop_condition, VerifyParams};

const DEFAULT_PDF: &str = "/Users/nanzhi/Downloads/CH 11 Persistence Monitor.pdf";

fn test_pdf_path() -> Option<String> {
    if let Ok(p) = std::env::var("PDF_TEST_PATH") {
        if Path::new(&p).exists() {
            return Some(p);
        }
    }
    if Path::new(DEFAULT_PDF).exists() {
        return Some(DEFAULT_PDF.to_string());
    }
    None
}

#[test]
fn loop_condition_text_layer_misses_path_image_ocr_recovers_it() {
    let Some(pdf) = test_pdf_path() else {
        eprintln!("SKIP: test PDF not found (set PDF_TEST_PATH)");
        return;
    };
    let backend = default_backend();
    let report = verify_loop_condition(&pdf, &VerifyParams::default(), backend.as_ref()).unwrap();

    for c in &report.conditions {
        eprintln!(
            "[{}] {} — {}",
            if c.pass { "PASS" } else { "FAIL" },
            c.description,
            c.detail
        );
    }
    assert!(
        report.all_pass,
        "loop condition failed: {:#?}",
        report.conditions
    );
    // The evidence must contain the exact path.
    assert!(
        report
            .ocr_text
            .iter()
            .any(|t| t.contains("com.apple.softwareupdate.plist")),
        "OCR output missing the path: {:#?}",
        report.ocr_text
    );
}

#[test]
fn loop_condition_fails_without_ocr() {
    // The whole point: disable OCR and the loop condition must FAIL.
    let Some(pdf) = test_pdf_path() else {
        eprintln!("SKIP: test PDF not found (set PDF_TEST_PATH)");
        return;
    };
    let report = verify_loop_condition(&pdf, &VerifyParams::default(), &NullOcr).unwrap();
    let ocr_cond = report
        .conditions
        .iter()
        .find(|c| c.id == "ocr_recovers_path")
        .expect("ocr condition present");
    assert!(!ocr_cond.pass, "without OCR the path must not be found");
}
