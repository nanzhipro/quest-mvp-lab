# quest-mvp-lab

A collection of self-contained **MVPs, technology evaluations, technical
examples, and demos**. Each experiment lives in its own directory and includes
its own README.

## Projects

| Project | What it validates | Stack | Status |
| ------- | ----------------- | ----- | ------ |
| [`es-mvp/`](es-mvp/) | Directory-scoped `AUTH_OPEN` enforcement using an inverted Endpoint Security muting strategy, with implementations compared across Objective-C, Rust, and Swift | Objective-C · Rust · Swift | Done — conclusions verified on real hardware; see `SPEC.md` §9 |
| [`es-process-mvp/`](es-process-mvp/) | Process-scoped PDF-open denial using an inverted process-muting strategy and bundle ID policy matching, with YAML configuration and separate discovery and monitoring clients | Swift | Done — 27 tests and real-device E2E validation all pass |
| [`es-procattr-mvp/`](es-procattr-mvp/) | ESF process attribution for AI agents: a process fingerprint (`pid`, `pidversion`, arguments, signing identity, and responsible process) feeds both a lineage chain and a file-behavior chain; includes a Santa-aligned `(pid, pidversion)` process tree, argument-based interpreter identity, and a single `NOTIFY`-only client | Swift · standard library only | Done — 30 tests and real-device E2E validation all pass |
| [`m7-chart/`](m7-chart/) | A multi-line stock performance chart for the Magnificent Seven, built with GPUI Component | Rust · GPUI 0.2.2 · gpui-component | Done |
| [`rust-appkit-bridge-mvp/`](rust-appkit-bridge-mvp/) | A seven-layer Rust-to-AppKit bridge comprising a Swift dynamic library, a Rust demo, and a Swift host | Rust · Swift · Objective-C | Done — `make verify` passes |
| [`ai-coding-agent-mvp/`](ai-coding-agent-mvp/) | A minimal agentic coding CLI with a tool-calling loop backed by an OpenAI-compatible endpoint | Python 3.9+ · standard library only | Done — all 54 tests pass; live E2E verified |
| [`pdf-inspector-mvp/`](pdf-inspector-mvp/) | A minimal Rust reimplementation of `firecrawl/pdf-inspector` with document classification, positioned text extraction, reading-order recovery, Markdown output, and embedded-image OCR through macOS Vision. Its self-check demonstrates that a path in Figure 11-2 on printed page 257 is absent from the text layer and recoverable only through OCR | Rust · lopdf · objc2/Vision | Done — all 38 tests and `pdfx verify` pass |
| [`deepseek-v4-flash-vision-exp/`](deepseek-v4-flash-vision-exp/) | An evaluation of the `deepseek-v4-flash-vision-exp` model across image understanding, creation, and generation tasks using local movie posters. Exercises both base64 and Files API inputs, plus a text-driven reconstruction loop (`image -> text -> SVG -> PNG`) with XML validation and a self-contained three-column comparison report. Fully reproducible from `SPEC.md` | Rust · reqwest · TDD (34 tests) | Done — the full pipeline passes; the model understands images but does not generate image files |
| [`fluid-motion-demo/`](fluid-motion-demo/) | A production-grade motion and interaction system for Apple platforms featuring spring-loaded buttons, hero transitions, an animated `MeshGradient` background, a Metal liquid-refraction shader, synchronized haptics, and a Liquid Glass aesthetic. Validates the six recipes in the Fluid Motion Design Language `DESIGN.md` | SwiftUI · Metal · SwiftPM | Done — all 3 tests pass; rendering verified with 86% color coverage in the `MeshGradient` |
| [`gmail-mcp-mvp/`](gmail-mcp-mvp/) | An OAuth-authenticated local MCP server for reading Gmail through the generally available Gmail REST API, with no Developer Preview dependency. Includes a desktop client, loopback authorization, three read-only tools that mirror the official Gmail MCP naming scheme, and one-command Hermes stdio integration | Python 3.13 · FastMCP (`mcp<2`) · google-api-python-client · uv | Done — all 14 tests pass, including end-to-end MCP wire-protocol coverage; live E2E awaits a real token |
| [`web-design-system-extractor-mvp/`](web-design-system-extractor-mvp/) | Evidence-first, multi-viewport webpage capture that produces a Google Labs `DESIGN.md`, DTCG tokens, CSS custom properties, and reviewable DOM, MHTML, HAR, resource, and screenshot evidence; includes default `robots.txt` enforcement, a reproducible capture fingerprint, and offline revalidation | Node.js 20+ · Playwright 1.62.1 · `@google/design.md` 0.4.0 · Ajv 8.20.0 · DTCG 2025.10 | Done — v0.3.0; all 10 tests and all 18 Respan Skill black-box checks pass, including official schema validation and cross-artifact parity |

## Conventions

- **One directory per MVP or demo.** Projects do not share code; each one is
  fully self-contained.
- **One README per project.** Every project README follows GitHub conventions
  and covers the background, key decisions, build, run, and verification
  commands, and the final conclusion.
- **Keep the index current.** Whenever a project is added, update the table
  above in the same change.
- **Bundle IDs.** Use the `com.nanzhipro.*` prefix when a bundle identifier is
  required. Keep demo artifacts unsigned unless distribution requires signing.

## Naming

Use `<topic>-mvp` for concept or technology validation and `<topic>-demo` for
showcase examples.
