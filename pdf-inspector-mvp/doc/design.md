# pdf-inspector-mvp — design

## Goal

A minimal, idiomatic Rust re-implementation of the core pipeline of
[firecrawl/pdf-inspector](https://github.com/firecrawl/pdf-inspector): classify a
PDF, extract positioned text, reconstruct reading order, convert to Markdown —
plus one stage upstream treats as optional: **OCR of embedded images via the
macOS Vision framework**.

The MVP is validated by a concrete loop condition on
`CH 11 Persistence Monitor.pdf`:

> Printed page 257 (PDF page 5) contains "Figure 11-2: A BlockBlock alert".
> The figure is a screenshot whose text layer does **not** contain the path
> `/Users/User/Library/LaunchAgents/com.apple.softwareupdate.plist` — the path
> exists only as pixels inside the raster image.

This proves the loop: a text-layer-only pipeline (the fast path) misses the
path; the image-OCR stage recovers it.

## Non-goals

- No table-structure recovery (upstream `tables/`).
- No newspaper/column heuristics beyond gap-based column splitting.
- No WebAssembly / Node / Python bindings.
- No OCR models on disk; Vision is the only OCR backend.

## Pipeline

```
PDF bytes ─► detector ─► extractor ─► layout ─► markdown ─► report
                 │            │  │
                 │            │  └─► images ─► ocr (Vision, macOS)
                 └────────────┴──► per-page signals
```

Modules mirror upstream layout:

| Module | Mirrors upstream | Scope (simplified) |
| ------ | ---------------- | ------------------ |
| `detector` | `detector.rs` | Classify TextBased/Scanned/ImageBased/Mixed from text-op counts per page; page-level OCR routing. |
| `extractor::content` | `extractor/content_stream.rs` | Walk content operators (Tf/Td/TD/Tm/T*/Tj/TJ/q/Q/cm/Do), emit positioned `TextItem`s. |
| `extractor::fonts` | `extractor/fonts.rs` | Font encoding resolution (WinAnsi/Standard + Differences), Type0/Identity-H ToUnicode, width tables. |
| `extractor::tounicode` | `tounicode.rs` | Minimal bfchar/bfrange CMap parser. |
| `extractor::images` | `extractor/xobjects.rs` | Image XObject extraction (JPEG passthrough; FlateDecode + predictors re-encoded to PNG). |
| `layout` | `extractor/layout.rs` + `reading_order.rs` | Baseline clustering into lines; gap-based column detection; running-head stripping. |
| `page_map` | (new) | Printed page number from running-head line ("… 257" → 257). |
| `markdown` | `markdown/convert.rs` | Headings via font-size ratio, lists, code blocks, image placeholders with captions, page breaks. |
| `ocr` | `vision/` | `OcrBackend` trait; macOS Vision via objc2; null backend elsewhere. |

## Public API (lib)

```rust
process_pdf(path, &PdfOptions) -> Result<PdfReport>
detect_document(&Document) -> Result<Detection>
extract_pages(&Document) -> Result<Vec<PageExtraction>>
to_markdown(&[PageSummary], &MarkdownOptions) -> String
```

Data types: `TextItem`, `PdfLine`, `ImageItem`, `OcrText`, `PageExtraction`,
`Detection`, `PdfReport`, `PdfType`, `PdfError`.

## Key decisions

1. **lopdf 0.42** — same parser as upstream; keeps the MVP honest about what a
   real PDF pipeline needs (content-stream ops, resource inheritance).
2. **Text items = per string-run** (Tj/TJ element), joined into lines later by
   baseline/gap analysis. Glyph-level advance is computed from font widths so
   x-position stays accurate; upstream emits per-run items too.
3. **OCR via objc2** — pure-Rust Vision bindings; the backend is a trait so
   the library compiles on non-macOS hosts with a null backend.
4. **Images re-encoded to PNG** for predictor-deflated streams; JPEG passes
   through untouched (both feed `VNImageRequestHandler` via `NSData`).
5. **Detector scans every page** for this MVP (docs are small); upstream
   samples. `ocr_recommended` is true whenever any page contains images —
   an honest signal that image-OCR may matter even for "TextBased" docs.
6. **Printed-page heuristic**: the top zone line is scanned for a standalone
   1–4 digit token; used to map printed page 257 → PDF page 5 for the
   self-check.

## Self-check (`pdfx verify`)

Conditions, in order:

1. Document parses; page count is reported.
2. Printed page 257 resolves to a PDF page (expect 5).
3. That page's **text layer** contains `Figure 11-2` and `A BlockBlock alert`.
4. The **text layer does not** contain `com.apple.softwareupdate.plist`
   (the fast path would miss it — this is the loop).
5. The page has ≥ 1 embedded image.
6. OCR of that image recovers the normalized path
   `/Users/User/Library/LaunchAgents/com.apple.softwareupdate.plist`.
7. Exit code 0 iff all conditions pass. `--no-ocr` demonstrates condition 6
   failing, proving the loop condition.

## Verification plan

- `cargo test` — unit tests (content ops, CMap, layout, page map, detector)
  plus integration tests on a generated PDF.
- `tests/selfcheck.rs` — the loop condition against the real PDF (skips with a
  message when the file is absent).
- `scripts/selftest.sh` — release build + `pdfx verify` end-to-end.
- `cargo clippy --all-targets -- -D warnings` and `cargo fmt --check`.
