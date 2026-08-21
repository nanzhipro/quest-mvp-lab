//! `pdfx` — command-line front-end for the pdf-inspector-mvp library.

use std::path::PathBuf;

use clap::{Parser, Subcommand};

use pdf_inspector_mvp::ocr::{default_backend, NullOcr};
use pdf_inspector_mvp::verify::{verify_loop_condition, VerifyParams};
use pdf_inspector_mvp::{extract_text_items, process_pdf, Result};

#[derive(Parser)]
#[command(
    name = "pdfx",
    version,
    about = "Minimal pdf-inspector in Rust: classify, extract, markdown, OCR-verify"
)]
struct Cli {
    #[command(subcommand)]
    cmd: Cmd,
}

#[derive(Subcommand)]
enum Cmd {
    /// Classify the document type.
    Detect {
        pdf: PathBuf,
        #[arg(long)]
        json: bool,
    },
    /// Dump per-page extracted text.
    Text {
        pdf: PathBuf,
        #[arg(long)]
        page: Option<u32>,
        #[arg(long)]
        plain: bool,
    },
    /// Convert to Markdown.
    Markdown { pdf: PathBuf },
    /// List embedded images.
    Images {
        pdf: PathBuf,
        #[arg(long)]
        page: Option<u32>,
    },
    /// Full JSON report.
    Report { pdf: PathBuf },
    /// Self-check: the page-257 loop condition.
    Verify {
        pdf: PathBuf,
        /// Printed page number to inspect (default: 257).
        #[arg(long, default_value_t = 257)]
        printed_page: u32,
        /// Expected path hidden inside the figure image.
        #[arg(
            long,
            default_value = "/Users/User/Library/LaunchAgents/com.apple.softwareupdate.plist"
        )]
        expected_path: String,
        /// Disable OCR — demonstrates the loop condition failing.
        #[arg(long, default_value_t = false)]
        no_ocr: bool,
    },
}

fn main() {
    let cli = Cli::parse();
    let result = run(cli);
    if let Err(e) = result {
        eprintln!("error: {e}");
        std::process::exit(2);
    }
}

fn run(cli: Cli) -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::from_default_env())
        .with_target(false)
        .compact()
        .try_init()
        .ok();

    match cli.cmd {
        Cmd::Detect { pdf, json } => {
            let det = pdf_inspector_mvp::detect(&pdf)?;
            if json {
                println!(
                    "{}",
                    serde_json::to_string_pretty(&serde_json::json!({
                        "pdf_type": det.pdf_type.as_str(),
                        "confidence": det.confidence,
                        "page_count": det.page_count,
                        "pages_with_text": det.pages_with_text,
                        "pages_with_images": det.pages_with_images,
                        "ocr_recommended": det.ocr_recommended,
                        "pages_needing_ocr": det.pages_needing_ocr,
                    }))
                    .unwrap()
                );
            } else {
                println!("pdf_type:      {}", det.pdf_type.as_str());
                println!("confidence:    {:.3}", det.confidence);
                println!("page_count:    {}", det.page_count);
                println!("text pages:    {}", det.pages_with_text);
                println!("image pages:   {}", det.pages_with_images);
                println!("ocr_recommended: {}", det.ocr_recommended);
                println!("pages_needing_ocr: {:?}", det.pages_needing_ocr);
            }
        }
        Cmd::Text { pdf, page, plain } => {
            let items = extract_text_items(&pdf)?;
            let filtered: Vec<_> = items
                .iter()
                .filter(|i| page.map(|p| i.page == p).unwrap_or(true))
                .collect();
            if plain {
                for it in &filtered {
                    print!("{}", it.text);
                }
                println!();
            } else {
                for it in &filtered {
                    println!(
                        "p{:03}  x {:7.1}  y {:7.1}  size {:4.1}  {:<28}  {}",
                        it.page,
                        it.x,
                        it.y,
                        it.size,
                        it.font.chars().take(28).collect::<String>(),
                        it.text
                    );
                }
            }
        }
        Cmd::Markdown { pdf } => {
            let report = process_pdf(&pdf)?;
            if let Some(md) = &report.markdown {
                print!("{md}");
            }
        }
        Cmd::Images { pdf, page } => {
            let doc = lopdf::Document::load(&pdf)
                .map_err(|e| pdf_inspector_mvp::PdfError::Parse(e.to_string()))?;
            let pages = pdf_inspector_mvp::extractor::extract_pages(&doc)?;
            for p in pages {
                if page.map(|pp| p.page == pp).unwrap_or(true) {
                    for img in &p.images {
                        println!(
                            "page {}  xref {}  {}x{}  {}  at ({:.0},{:.0}) {}x{}",
                            img.page,
                            img.xref,
                            img.width,
                            img.height,
                            img.format,
                            img.x,
                            img.y,
                            img.w,
                            img.h
                        );
                    }
                }
            }
        }
        Cmd::Report { pdf } => {
            let report = process_pdf(&pdf)?;
            println!("{}", serde_json::to_string_pretty(&report).unwrap());
        }
        Cmd::Verify {
            pdf,
            printed_page,
            expected_path,
            no_ocr,
        } => {
            let params = VerifyParams {
                printed_page,
                expected_path,
                use_ocr: !no_ocr,
                ..VerifyParams::default()
            };
            let backend: Box<dyn pdf_inspector_mvp::ocr::OcrBackend> = if no_ocr {
                Box::new(NullOcr)
            } else {
                default_backend()
            };
            let report = verify_loop_condition(&pdf, &params, backend.as_ref())?;

            println!("== pdfx verify — self-check loop condition ==");
            println!("pdf:        {}", pdf.display());
            println!("ocr backend: {}", backend.name());
            println!();
            for c in &report.conditions {
                let mark = if c.pass { "PASS" } else { "FAIL" };
                println!("[{mark}] {}", c.description);
                println!("      {}", c.detail);
            }
            println!();
            if report.all_pass {
                println!("RESULT: ALL CHECKS PASSED — the loop condition holds.");
                println!("(text layer misses the path; image OCR recovers it)");
            } else {
                println!("RESULT: CHECK FAILED");
                println!("OCR output (target page images):");
                for t in &report.ocr_text {
                    println!("  | {t}");
                }
                std::process::exit(1);
            }
        }
    }
    Ok(())
}
