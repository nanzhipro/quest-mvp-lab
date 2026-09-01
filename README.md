# quest-mvp-lab

> A collection of self-contained **MVPs, technology evaluations, technical
> examples, and demos** — mostly around macOS Endpoint Security, AI agents, and
> design systems.

![Experiments](https://img.shields.io/badge/experiments-14-blue.svg)
![Status](https://img.shields.io/badge/status-active-brightgreen.svg)
![Platform](https://img.shields.io/badge/focus-macOS%20%C2%B7%20AI%20agents%20%C2%B7%20design%20systems-lightgrey.svg)

Each experiment lives in its own directory, is fully self-contained (no shared
code between projects), and ships its own README covering the background, key
decisions, build/run/verify commands, and the final conclusion.

## Table of Contents

- [Highlights](#highlights)
- [Projects](#projects)
- [Repository Structure](#repository-structure)
- [Getting Started](#getting-started)
- [Conventions](#conventions)
- [Naming](#naming)

## Highlights

The experiments cluster around five themes:

- **macOS Endpoint Security.** A progression of ESF (Endpoint Security
  Framework) studies: [`es-mvp/`](es-mvp/) proves directory-scoped `AUTH_OPEN`
  enforcement via inverted muting, [`es-process-mvp/`](es-process-mvp/) narrows
  that to per-process denial, [`es-procattr-mvp/`](es-procattr-mvp/) adds
  process attribution and lineage tracking, and
  [`es-agent-sensor-mvp/`](es-agent-sensor-mvp/) turns it all into a
  production-shaped behavior sensor for AI agents.
- **AI agents and prompt-injection defense.**
  [`ai-coding-agent-mvp/`](ai-coding-agent-mvp/) is a minimal agentic coding
  CLI; [`promptguard-llamafirewall-mvp/`](promptguard-llamafirewall-mvp/)
  benchmarks Meta PromptGuard 2 and LlamaFirewall locally, and
  [`promptguard-finetune-mvp/`](promptguard-finetune-mvp/) fine-tunes away the
  domain false positives found there.
- **Design systems and UI.**
  [`web-design-system-extractor-mvp/`](web-design-system-extractor-mvp/)
  captures any site into an evidence-backed design system, and
  [`substack-design-system-demo/`](substack-design-system-demo/) validates the
  extracted tokens interactively; [`fluid-motion-demo/`](fluid-motion-demo/)
  lands a six-recipe motion language in SwiftUI + Metal;
  [`m7-chart/`](m7-chart/) explores GPUI Component charting with a live
  seven-stock trend overlay.
- **Documents and multimodal models.**
  [`pdf-inspector-mvp/`](pdf-inspector-mvp/) reimplements
  `firecrawl/pdf-inspector` in Rust with OCR fallback, and
  [`deepseek-v4-flash-vision-exp/`](deepseek-v4-flash-vision-exp/) evaluates a
  vision model's image understanding and reconstruction abilities.
- **Cross-language bridging.**
  [`rust-appkit-bridge-mvp/`](rust-appkit-bridge-mvp/) demonstrates a
  seven-layer Rust ↔ AppKit interop stack.

## Projects

| Project | What it validates | Stack | Status |
| ------- | ----------------- | ----- | ------ |
| [`es-mvp/`](es-mvp/) | Directory-scoped `AUTH_OPEN` enforcement using an inverted Endpoint Security muting strategy, with implementations compared across Objective-C, Rust, and Swift | Objective-C · Rust · Swift | Done — conclusions verified on real hardware; see `SPEC.md` §9 |
| [`es-process-mvp/`](es-process-mvp/) | Process-scoped PDF-open denial using an inverted process-muting strategy and bundle ID policy matching, with YAML configuration and separate discovery and monitoring clients | Swift | Done — 27 tests and real-device E2E validation all pass |
| [`es-procattr-mvp/`](es-procattr-mvp/) | ESF process attribution for AI agents: a process fingerprint (`pid`, `pidversion`, arguments, signing identity, and responsible process) feeds both a lineage chain and a file-behavior chain; includes a Santa-aligned `(pid, pidversion)` process tree, argument-based interpreter identity, and a single `NOTIFY`-only client | Swift · standard library only | Done — 30 tests and real-device E2E validation all pass |
| [`m7-chart/`](m7-chart/) | A multi-line stock performance chart for the Magnificent Seven, built with GPUI Component's low-level plot primitives on a single shared coordinate system | Rust · GPUI 0.2.2 · gpui-component 0.5.1 | Done |
| [`rust-appkit-bridge-mvp/`](rust-appkit-bridge-mvp/) | A seven-layer Rust-to-AppKit bridge comprising a Swift dynamic library, a Rust demo, and a Swift host | Rust · Swift · Objective-C | Done — `make verify` passes |
| [`ai-coding-agent-mvp/`](ai-coding-agent-mvp/) | A minimal agentic coding CLI with a tool-calling loop backed by an OpenAI-compatible endpoint | Python 3.9+ · standard library only | Done — all 54 tests pass; live E2E verified |
| [`pdf-inspector-mvp/`](pdf-inspector-mvp/) | A minimal Rust reimplementation of `firecrawl/pdf-inspector` with document classification, positioned text extraction, reading-order recovery, Markdown output, and embedded-image OCR through macOS Vision. Its self-check demonstrates that a path in Figure 11-2 on printed page 257 is absent from the text layer and recoverable only through OCR | Rust · lopdf · objc2/Vision | Done — all 38 tests and `pdfx verify` pass |
| [`deepseek-v4-flash-vision-exp/`](deepseek-v4-flash-vision-exp/) | An evaluation of the `deepseek-v4-flash-vision-exp` model across image understanding, creation, and generation tasks using local movie posters. Exercises both base64 and Files API inputs, plus a text-driven reconstruction loop (`image -> text -> SVG -> PNG`) with XML validation and a self-contained three-column comparison report. Fully reproducible from `SPEC.md` | Rust · reqwest · TDD (34 tests) | Done — the full pipeline passes; the model understands images but does not generate image files |
| [`fluid-motion-demo/`](fluid-motion-demo/) | A production-grade motion and interaction system for Apple platforms featuring spring-loaded buttons, hero transitions, an animated `MeshGradient` background, a Metal liquid-refraction shader, synchronized haptics, and a Liquid Glass aesthetic. Validates the six recipes in the Fluid Motion Design Language `DESIGN.md` | SwiftUI · Metal · SwiftPM | Done — all 3 tests pass; rendering verified with 86% color coverage in the `MeshGradient` |
| [`web-design-system-extractor-mvp/`](web-design-system-extractor-mvp/) | Evidence-first, multi-viewport webpage capture that produces an agent-facing Google Labs `DESIGN.md`, canonical DTCG tokens, browser-validated CSS custom properties, and reviewable DOM, MHTML, HAR, resource, and screenshot evidence; includes default `robots.txt` enforcement, a reproducible capture fingerprint, and offline revalidation | Node.js 20+ · Playwright 1.62.1 · `@google/design.md` 0.4.0 · Ajv 8.20.0 · DTCG 2025.10 | Done — v0.4.0; all 10 tests and the Firecrawl 20/20 black-box release gates pass, including official schema validation, 72 typed DESIGN/DTCG bindings, one-to-one parity for 165 CSS variables, and 165 Chromium consumption probes |
| [`substack-design-system-demo/`](substack-design-system-demo/) | Interactive validation console for the extracted Substack design system, exercising every CSS token plus observed color, type, spacing, radius, shadow, gradient, navigation, editorial-content, state, motion, and responsive pattern | HTML · CSS · JavaScript · CSSOM | Done — 88/88 runtime token probes plus desktop and mobile visual QA |
| [`promptguard-llamafirewall-mvp/`](promptguard-llamafirewall-mvp/) | Local deployment of Meta PromptGuard 2 (86M) and LlamaFirewall for jailbreak and indirect prompt-injection detection on an agent's user-message and tool-result surfaces, with an MD5-verified gated-model mirror, a 19-sample EN/ZH labeled corpus, and per-engine accuracy/TPR/FPR evaluation | Python 3.13 · uv · transformers · llamafirewall | Done — all 12 tests pass; 100% attack recall on both engines, 94.7% accuracy at the framework's 0.9 block threshold |
| [`promptguard-finetune-mvp/`](promptguard-finetune-mvp/) | Fine-tuning PromptGuard 2 (86M) on 1,487 LLM-generated synthetic samples (attack families × carriers × EN/ZH plus hard negatives) to fix domain false positives for the ADR agent scenario, with a three-run iteration log (v1 OOD-FP regression → v2/v3 fixes) and base-vs-finetuned evaluation on a never-trained-on corpus | Python 3.13 · uv · transformers · Trainer on Apple-Silicon MPS | Done — all 7 tests pass; corpus FPR 25%→12.5% (0% at threshold 0.9) with 100% attack recall kept |
| [`es-agent-sensor-mvp/`](es-agent-sensor-mvp/) | NOTIFY-only Endpoint Security sensor for AI-agent behavior: a Santa-style `(pid, pidversion)` process tree with agent annotation, bidirectional actor/target matching, and a normalized Actor–Operation–Target JSONL schema; Unix-socket IPC via ES plus a libproc net-poller compensating for the ESF TCP/UDP blind spot | Rust · C shim (libEndpointSecurity) | Done — 86 unit tests pass with 96.7% line coverage; scripted E2E (fake-agent assertions) and a 35s live Hermes smoke (1749 matched events, zero drops) both pass on macOS 26.5.2 |

## Repository Structure

```
quest-mvp-lab/
├── es-mvp/                            # Directory-scoped ES AUTH_OPEN (ObjC/Rust/Swift)
├── es-process-mvp/                    # Process-scoped ES denial
├── es-procattr-mvp/                   # ESF process attribution for AI agents
├── es-agent-sensor-mvp/               # NOTIFY-only ES behavior sensor (Rust)
├── ai-coding-agent-mvp/               # Minimal agentic coding CLI (Python)
├── promptguard-llamafirewall-mvp/     # PromptGuard 2 + LlamaFirewall benchmark
├── promptguard-finetune-mvp/          # PromptGuard 2 fine-tuning (Apple-Silicon MPS)
├── web-design-system-extractor-mvp/   # Evidence-first design-system capture (Playwright)
├── substack-design-system-demo/       # Extracted-token validation console
├── fluid-motion-demo/                 # SwiftUI + Metal motion language demo
├── m7-chart/                          # GPUI Component multi-line chart
├── pdf-inspector-mvp/                 # PDF structure extraction + OCR (Rust)
├── deepseek-v4-flash-vision-exp/      # Vision-model evaluation pipeline
├── rust-appkit-bridge-mvp/            # Rust ↔ AppKit seven-layer bridge
└── README.md                          # You are here
```

## Getting Started

There is no top-level build — every project is independent:

```bash
cd <project-dir>     # pick one from the table above
cat README.md        # background, decisions, build/run/verify commands
```

Each project README is the single source of truth for its toolchain (SwiftPM,
Cargo, uv, npm, …) and its one-command verification (tests, `make verify`,
E2E scripts).

## Conventions

- **One directory per MVP or demo.** Projects do not share code; each one is
  fully self-contained.
- **One README per project.** Every project README follows GitHub conventions
  and covers the background, key decisions, build, run, and verification
  commands, and the final conclusion.
- **Keep the index current.** Whenever a project is added, updated, or
  removed, the table and the structure tree above are updated in the same
  change.
- **Bundle IDs.** Use the `com.nanzhipro.*` prefix when a bundle identifier is
  required. Keep demo artifacts unsigned unless distribution requires signing.

## Naming

Use `<topic>-mvp` for concept or technology validation and `<topic>-demo` for
showcase examples.
