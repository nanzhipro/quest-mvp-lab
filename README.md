# quest-mvp-lab

A playground for **MVP concepts, tech-selection validations, technical examples, and demos**.
Each experiment lives in its own directory with a self-contained README.

## Projects

| Project | What it validates | Stack | Status |
| ------- | ----------------- | ----- | ------ |
| [`es-mvp/`](es-mvp/) | Directory-scoped AUTH_OPEN enforcement via Endpoint Security muting inversion (ObjC / Rust / Swift comparison) | ObjC · Rust · Swift | Done — real-device conclusions in `SPEC.md` §9 |
| [`es-process-mvp/`](es-process-mvp/) | Process-scoped PDF-open DENY enforcement via process muting inversion + bundleId policy matching (YAML config, two-client discovery + monitor) | Swift | Done — 27 tests + real-device e2e ALL PASS |
| [`m7-chart/`](m7-chart/) | Multi-line stock trend chart (Magnificent 7) with GPUI Component | Rust · gpui 0.2.2 · gpui-component | Done |
| [`rust-appkit-bridge-mvp/`](rust-appkit-bridge-mvp/) | 7-layer Rust ↔ AppKit bridging (Swift dylib + Rust demo + Swift host) | Rust · Swift · ObjC | Done — `make verify` all green |
| [`ai-coding-agent-mvp/`](ai-coding-agent-mvp/) | Minimal agentic coding CLI: tool-use loop over an OpenAI-compatible endpoint | Python 3.9+ · stdlib only | Done — 54 tests green, live E2E verified |
| [`pdf-inspector-mvp/`](pdf-inspector-mvp/) | Minimal Rust re-implementation of firecrawl/pdf-inspector: classify, positioned extraction, reading order, Markdown, embedded-image OCR (macOS Vision). Self-check loop: path inside Figure 11-2 on printed page 257 is invisible to the text layer, recovered only by OCR | Rust · lopdf · objc2/Vision | Done — 38 tests green, `pdfx verify` all PASS |
| [`deepseek-v4-flash-vision-exp/`](deepseek-v4-flash-vision-exp/) | DeepSeek vision model (`deepseek-v4-flash-vision-exp`) capability validation: understanding / creation / generation probe on local movie posters; base64 + Files API dual image paths; text-driven SVG recreation loop (image → text → SVG → PNG) with XML validation; single-file 3-column compare report. Reproducible via `SPEC.md` | Rust · reqwest · TDD (34 tests) | Done — full pipeline PASS, no image-file generation (text-output understanding model) |

## Conventions

- **One directory per MVP/demo.** No shared code between projects — each stands alone.
- **README per project.** Every project ships a README following GitHub best practices: background, key decisions, build/run/verify commands, and the conclusion.
- **Keep the index in sync.** Adding a project means updating the table above (and the root `README.md` of this repo).
- **Bundle IDs** use the `com.nanzhipro.*` prefix when needed. Demo artifacts stay unsigned unless distribution requires otherwise.

## Naming

`<topic>-mvp` for concept / tech-selection validation, `<topic>-demo` for example showcases.
