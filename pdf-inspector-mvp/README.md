# pdf-inspector-mvp

A minimal, idiomatic Rust re-implementation of the core pipeline of
[firecrawl/pdf-inspector](https://github.com/firecrawl/pdf-inspector): PDF
classification, positioned text extraction, reading-order layout, Markdown
conversion — plus one stage upstream treats as optional: **OCR of embedded
images via the macOS Vision framework**.

The MVP is validated by a concrete self-check loop condition on a real book
chapter (`CH 11 Persistence Monitor.pdf`, 26 pages):

> Printed page **257** (PDF page 5) contains the figure
> **"Figure 11-2: A BlockBlock alert"**. The figure is a screenshot whose
> **text layer does not** contain the path
> `/Users/User/Library/LaunchAgents/com.apple.softwareupdate.plist` — the path
> exists only as pixels inside the raster image.

`pdfx verify` proves the loop: a text-layer-only pipeline misses the path; the
image-OCR stage recovers it. `--no-ocr` demonstrates the loop failing.

## Quick start

```bash
cargo build --release

# classify
./target/release/pdfx detect chapter.pdf

# positioned text
./target/release/pdfx text chapter.pdf --page 5

# markdown
./target/release/pdfx markdown chapter.pdf > chapter.md

# images
./target/release/pdfx images chapter.pdf

# full JSON report
./target/release/pdfx report chapter.pdf

# self-check loop condition (page 257 / figure 11-2)
./target/release/pdfx verify '/Users/nanzhi/Downloads/CH 11 Persistence Monitor.pdf'
./target/release/pdfx verify --no-ocr ...   # fails: proves OCR is required
```

## Architecture

```
PDF bytes ─► detector ─► extractor ─► layout ─► markdown ─► report
                 │            │  │
                 │            │  └─► images ─► ocr (Vision, macOS)
                 └────────────┴──► per-page signals
```

| Module | Mirrors upstream | Scope (simplified) |
| ------ | ---------------- | ------------------ |
| `detector` | `detector.rs` | TextBased / Scanned / ImageBased / Mixed from text-op counts; page-level OCR routing. |
| `extractor::content` | `content_stream.rs` | Content-stream walker (Tf/Td/Tm/Tj/TJ/q/Q/cm/Do), positioned `TextItem`s, Form XObjects. |
| `extractor::fonts` | `fonts.rs` | Encoding resolution (WinAnsi/Standard + Differences), Type0/Identity-H ToUnicode, width tables. |
| `extractor::tounicode` | `tounicode.rs` | Minimal bfchar/bfrange CMap parser (UTF-16BE/UTF-8/Latin-1). |
| `extractor::images` | `xobjects.rs` | JPEG passthrough; FlateDecode + predictors (TIFF/PNG) re-encoded to PNG. |
| `layout` | `layout.rs` + `reading_order.rs` | Baseline clustering into lines, gap-based columns, y-flip detection, running-head stripping. |
| `page_map` | (new) | Printed page number from the running head ("… 257" → 257). |
| `markdown` | `convert.rs` | Headings via font-size mode ratio, lists, code blocks, image placeholders with captions, page breaks. |
| `ocr` | `vision/` | `OcrBackend` trait; macOS Vision via pure-Rust objc2; null backend elsewhere. |
| `verify` | (new) | The self-check loop condition as a library + CLI subcommand. |

## Key decisions

1. **lopdf 0.42** — same parser as upstream; the walker handles real-world
   quirks found in the test book: `1 Tf` + scaled text matrices (old
   InDesign), per-glyph TJ arrays with kerning, non-breaking-space glyphs,
   and a y-flipped coordinate system (top-left origin).
2. **Effective font size** = `Tfs × Tm[0]` — required for correct glyph
   advances, kerning, and heading detection on InDesign-produced PDFs.
3. **OCR via objc2** — pure-Rust Vision bindings (`VNRecognizeTextRequest`);
   the backend is a trait, so the library compiles on non-macOS hosts with a
   null backend.
4. **Printed-page mapping** — the running head line is scanned for standalone
   or embedded 1–4 digit tokens; used to map printed page 257 → PDF page 5.
5. **Body size = font-size mode** — body text is the most common line size;
   headings, captions, running heads, and footnotes are outliers.
6. **`ocr_recommended` when any page has images** — an honest signal that
   image OCR may matter even for TextBased documents (exactly the loop
   condition's lesson).

## Verification

```bash
cargo test                                   # 38 tests: units + generated-PDF pipeline + self-check
scripts/selftest.sh                          # release build + detect + verify
cargo clippy --all-targets                   # clean, zero warnings
cargo fmt --check
```

`tests/selfcheck.rs` runs the loop condition against the real PDF (skips with
a message when the file is absent; override the path with `PDF_TEST_PATH`).

### Self-check result (real document)

```
[PASS] document has 26 pages (expected 26)
[PASS] printed page 257 maps to PDF page 5
[PASS] text layer contains "Figure 11-2"
[PASS] text layer contains "A BlockBlock alert"
[PASS] text layer does NOT contain com.apple.softwareupdate.plist (fast path misses it)
[PASS] page 5 has at least one embedded image
[PASS] image OCR recovers /Users/User/Library/LaunchAgents/com.apple.softwareupdate.plist
RESULT: ALL CHECKS PASSED — the loop condition holds.
```

The Markdown for page 5 places the figure inline with its caption:

```markdown
![Figure!11-2:!A BlockBlock alert](assets/page-5-58.jpg)

Figure!11-2:!A BlockBlock alert
```

(The `!` is the book's non-breaking-space glyph, decoded as-is from the font.)

## Non-goals (vs upstream)

- No table-structure recovery (`tables/`).
- No newspaper/column heuristics beyond gap-based column splitting.
- No WebAssembly / Node / Python bindings.
- No model-based OCR on disk; Vision is the only OCR backend.

## Conclusion

The MVP proves the end-to-end pipeline on a real, gnarly PDF: a 2005-era
InDesign book chapter that defeats naive extractors (size-1 fonts, flipped
coordinates, per-glyph kerning, NBSP glyphs, screenshot figures). Text-layer
extraction alone misses the path inside Figure 11-2; the Vision OCR stage
recovers it exactly. The loop condition is now a repeatable test
(`pdfx verify` / `tests/selfcheck.rs`), not a manual inspection.

Design doc: [`doc/design.md`](doc/design.md).
