# quest-mvp-lab

> A collection of self-contained **MVPs, technology evaluations, technical
> examples, and demos** — mostly around macOS Endpoint Security, AI agents, and
> design systems.

![Experiments](https://img.shields.io/badge/experiments-23-blue.svg)
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

The experiments cluster around ten themes:

- **macOS Endpoint Security.** A progression of ESF (Endpoint Security
  Framework) studies: [`es-mvp/`](es-mvp/) proves directory-scoped `AUTH_OPEN`
  enforcement via inverted muting, [`es-process-mvp/`](es-process-mvp/) narrows
  that to per-process denial, [`es-procattr-mvp/`](es-procattr-mvp/) adds
  process attribution and lineage tracking, and
  [`es-agent-sensor-mvp/`](es-agent-sensor-mvp/) turns it all into a
  production-shaped behavior sensor for AI agents.
  [`outlook-dlp-mvp/`](outlook-dlp-mvp/) points the same primitives at outbound
  mail — an `AUTH_CREATE` trigger on Outlook's sync logs, HxStore mail
  carving, official cel-cpp policy evaluation, and a three-state disposition
  (audit / approval-with-timeout / block) — and is **local-only** for now (see
  the note under the project table).
- **AI agents and prompt-injection defense.**
  [`react-loop-mvp/`](react-loop-mvp/) is a 28-line hand-written ReAct loop with
  no framework at all (stdlib only) and the native tool-calling variant of the
  same loop for contrast; [`agent-guardrails-mvp/`](agent-guardrails-mvp/) puts
  five deterministic guardrails around an agent's tool calls — a default-deny
  gate, a budget circuit breaker, taint tracking, output redaction and a
  hash-chained audit trail — and lets a real DeepSeek loop try to breach them;
  [`ai-coding-agent-mvp/`](ai-coding-agent-mvp/) is a
  minimal agentic coding CLI;
  [`promptguard-llamafirewall-mvp/`](promptguard-llamafirewall-mvp/)
  benchmarks Meta PromptGuard 2 and LlamaFirewall locally, and
  [`promptguard-finetune-mvp/`](promptguard-finetune-mvp/) fine-tunes away the
  domain false positives found there.
- **Agent tool-call sandboxing.**
  [`sandbox-center-mvp/`](sandbox-center-mvp/) reproduces the process architecture
  behind an agent's tool calls — a resident policy center, a one-shot CLI per
  call, and Seatbelt enforcement — with kernel-denial reclamation and a
  hash-chained audit trail.
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
- **Workflow orchestration.**
  [`airflow-mini-mvp/`](airflow-mini-mvp/) re-implements Apache Airflow's core
  mental model — DAG authoring, scheduling with catchup, the TaskInstance
  state machine, executors, retries, XCom, and a metadata DB — in under ~1k
  lines of stdlib-only Python.
  [`supervisor-graph-mvp/`](supervisor-graph-mvp/) splits an agent
  orchestration layer into two readable pieces — a lightweight-model Supervisor
  (intent recognition, DAG planning, routing, aggregation) over an explicit
  state graph whose `check` node can route work back for repair or escalate to a
  human — with four deterministic specialist agents and nine consistency rules.
- **LLM wire-level observability.**
  [`llm-heartbeat-io-mvp/`](llm-heartbeat-io-mvp/) records what a minimal
  `ping` actually puts on the wire: a recording reverse proxy dumps every
  request and response body, dissects the assembled system prompt block by
  block with CJK-aware token estimates reconciled against the provider's
  `usage`, and runs a controlled A/B that prices the project-context injection
  (one heartbeat = 154,340 B / 38,718 prompt tokens vs 90 B / 31 tokens for a
  bare call).
- **Engineering practice.**
  [`ai-course-engineering-mvp/`](ai-course-engineering-mvp/) is the lab's only
  teaching artifact: four offline exercises (preserve behaviour, migrate a ring
  queue, retry idempotently, verify both sides of a delivery claim) shipped
  with a contract suite that passes on the reference implementation and fails
  on a deliberately faulty one.
- **Retrieval and knowledge engineering.**
  [`graphrag-hybrid-mvp/`](graphrag-hybrid-mvp/) builds one Chinese document
  corpus into four retrieval channels at once — BM25, Chinese ONNX embeddings,
  an LLM-extracted knowledge graph, and community summaries — fuses them with
  RRF, and answers through DeepSeek with per-claim citations. It ships a
  labelled question set and a channel-ablation harness, so the multi-hop gain
  from the graph channel is measured rather than asserted.

## Projects

| Project | What it validates | Stack | Status |
| ------- | ----------------- | ----- | ------ |
| [`es-mvp/`](es-mvp/) | Directory-scoped `AUTH_OPEN` enforcement using an inverted Endpoint Security muting strategy, with implementations compared across Objective-C, Rust, and Swift | Objective-C · Rust · Swift | Done — conclusions verified on real hardware; see `SPEC.md` §9 |
| [`es-process-mvp/`](es-process-mvp/) | Process-scoped PDF-open denial using an inverted process-muting strategy and bundle ID policy matching, with YAML configuration and separate discovery and monitoring clients | Swift | Done — 27 tests and real-device E2E validation all pass |
| [`es-procattr-mvp/`](es-procattr-mvp/) | ESF process attribution for AI agents: a process fingerprint (`pid`, `pidversion`, arguments, signing identity, and responsible process) feeds both a lineage chain and a file-behavior chain; includes a Santa-aligned `(pid, pidversion)` process tree, argument-based interpreter identity, and a single `NOTIFY`-only client | Swift · standard library only | Done — 30 tests and real-device E2E validation all pass |
| [`m7-chart/`](m7-chart/) | A multi-line stock performance chart for the Magnificent Seven, built with GPUI Component's low-level plot primitives on a single shared coordinate system | Rust · GPUI 0.2.2 · gpui-component 0.5.1 | Done |
| [`rust-appkit-bridge-mvp/`](rust-appkit-bridge-mvp/) | A seven-layer Rust-to-AppKit bridge comprising a Swift dynamic library, a Rust demo, and a Swift host | Rust · Swift · Objective-C | Done — `make verify` passes |
| [`ai-coding-agent-mvp/`](ai-coding-agent-mvp/) | A minimal agentic coding CLI with a tool-calling loop backed by an OpenAI-compatible endpoint | Python 3.9+ · standard library only | Done — all 54 tests pass; live E2E verified |
| [`react-loop-mvp/`](react-loop-mvp/) | A hand-written 28-line ReAct loop (text protocol, parsed by the project itself) against DeepSeek, plus the native function-calling variant of the same loop and tools for a same-question comparison; ships an offline scripted-model demo, per-step JSONL plus raw wire evidence, and a guard suite for protocol drift | Python 3.9+ · standard library only | Done — 187 offline tests pass; three live E2E runs verified against DeepSeek (hand-written text protocol used 51% of the native variant's input tokens for the same answer); see `SPEC.md` |
| [`agent-guardrails-mvp/`](agent-guardrails-mvp/) | Five deterministic guardrails around an agent's tool calls, all outside the model: a default-deny gate (8 ordered rules incl. taint-tightening and approval), a budget circuit breaker (steps/tokens/time/tool-calls/identical-arguments), output redaction, and a hash-chain audit trail that can be re-verified offline; driven by a real DeepSeek loop or a deterministic scripted "hijacked model", with six scenarios, four safety invariants, structured event stream (23 event names) and timestamped evidence runs | Python 3.9+ · standard library only | Done — 114 offline tests + 2 live E2E tests pass; live DeepSeek runs blocked a real injection-driven exfiltration, refused an irreversible delete, and tripped the loop breaker on the third identical fetch |
| [`pdf-inspector-mvp/`](pdf-inspector-mvp/) | A minimal Rust reimplementation of `firecrawl/pdf-inspector` with document classification, positioned text extraction, reading-order recovery, Markdown output, and embedded-image OCR through macOS Vision. Its self-check demonstrates that a path in Figure 11-2 on printed page 257 is absent from the text layer and recoverable only through OCR | Rust · lopdf · objc2/Vision | Done — all 38 tests and `pdfx verify` pass |
| [`deepseek-v4-flash-vision-exp/`](deepseek-v4-flash-vision-exp/) | An evaluation of the `deepseek-v4-flash-vision-exp` model across image understanding, creation, and generation tasks using local movie posters. Exercises both base64 and Files API inputs, plus a text-driven reconstruction loop (`image -> text -> SVG -> PNG`) with XML validation and a self-contained three-column comparison report. Fully reproducible from `SPEC.md` | Rust · reqwest · TDD (34 tests) | Done — the full pipeline passes; the model understands images but does not generate image files |
| [`fluid-motion-demo/`](fluid-motion-demo/) | A production-grade motion and interaction system for Apple platforms featuring spring-loaded buttons, hero transitions, an animated `MeshGradient` background, a Metal liquid-refraction shader, synchronized haptics, and a Liquid Glass aesthetic. Validates the six recipes in the Fluid Motion Design Language `DESIGN.md` | SwiftUI · Metal · SwiftPM | Done — all 3 tests pass; rendering verified with 86% color coverage in the `MeshGradient` |
| [`web-design-system-extractor-mvp/`](web-design-system-extractor-mvp/) | Evidence-first, multi-viewport webpage capture that produces an agent-facing Google Labs `DESIGN.md`, canonical DTCG tokens, browser-validated CSS custom properties, and reviewable DOM, MHTML, HAR, resource, and screenshot evidence; includes default `robots.txt` enforcement, a reproducible capture fingerprint, and offline revalidation | Node.js 20+ · Playwright 1.62.1 · `@google/design.md` 0.4.0 · Ajv 8.20.0 · DTCG 2025.10 | Done — v0.4.0; all 10 tests and the Firecrawl 20/20 black-box release gates pass, including official schema validation, 72 typed DESIGN/DTCG bindings, one-to-one parity for 165 CSS variables, and 165 Chromium consumption probes |
| [`substack-design-system-demo/`](substack-design-system-demo/) | Interactive validation console for the extracted Substack design system, exercising every CSS token plus observed color, type, spacing, radius, shadow, gradient, navigation, editorial-content, state, motion, and responsive pattern | HTML · CSS · JavaScript · CSSOM | Done — 88/88 runtime token probes plus desktop and mobile visual QA |
| [`promptguard-llamafirewall-mvp/`](promptguard-llamafirewall-mvp/) | Local deployment of Meta PromptGuard 2 (86M) and LlamaFirewall for jailbreak and indirect prompt-injection detection on an agent's user-message and tool-result surfaces, with an MD5-verified gated-model mirror, a 19-sample EN/ZH labeled corpus, and per-engine accuracy/TPR/FPR evaluation | Python 3.13 · uv · transformers · llamafirewall | Done — all 12 tests pass; 100% attack recall on both engines, 94.7% accuracy at the framework's 0.9 block threshold |
| [`promptguard-finetune-mvp/`](promptguard-finetune-mvp/) | Fine-tuning PromptGuard 2 (86M) on 1,487 LLM-generated synthetic samples (attack families × carriers × EN/ZH plus hard negatives) to fix domain false positives for the ADR agent scenario, with a three-run iteration log (v1 OOD-FP regression → v2/v3 fixes) and base-vs-finetuned evaluation on a never-trained-on corpus | Python 3.13 · uv · transformers · Trainer on Apple-Silicon MPS | Done — all 7 tests pass; corpus FPR 25%→12.5% (0% at threshold 0.9) with 100% attack recall kept |
| [`es-agent-sensor-mvp/`](es-agent-sensor-mvp/) | NOTIFY-only Endpoint Security sensor for AI-agent behavior: a Santa-style `(pid, pidversion)` process tree with agent annotation, bidirectional actor/target matching, and a normalized Actor–Operation–Target JSONL schema; Unix-socket IPC via ES plus a libproc net-poller compensating for the ESF TCP/UDP blind spot | Rust · C shim (libEndpointSecurity) | Done — 86 unit tests pass with 96.7% line coverage; scripted E2E (fake-agent assertions) and a 35s live Hermes smoke (1749 matched events, zero drops) both pass on macOS 26.5.2 |
| [`outlook-dlp-mvp/`](outlook-dlp-mvp/) | Outbound-mail DLP interception for Outlook for Mac: directory-scoped ESF `AUTH_CREATE` trigger on OSA sync logs, HxStore.hxd mail carving, official cel-cpp policy evaluation, three-state disposition (audit / approval-with-timeout / block), and JSONL + os_log trace-audited pipeline | ObjC++ · CEL (cel-cpp) · ESF | Done — 117 tests green; live e2e verified detection/audit/approval-timeout, physical-block retest pending |
| [`ai-course-engineering-mvp/`](ai-course-engineering-mvp/) | Four offline exercises for AI-assisted changes: preserve existing behavior, migrate a ring queue, retry idempotently, and verify both sides of delivery evidence | Python 3.10+ · standard library only | Reference contract suite passes; intentionally faulty learner implementation fails as expected |
| [`airflow-mini-mvp/`](airflow-mini-mvp/) | A faithful toy-scale re-implementation of Apache Airflow's core concepts: DAG context-manager authoring with `@dag.task`/`PythonOperator` and `>>` wiring with cycle detection, cron/timedelta scheduling with Airflow catchup semantics, the TaskInstance state machine with retries and `up_for_retry`, XCom return-value passing, Sequential/Local executors, a SQLite metadata DB, and an `airflow`-style `miniflow` CLI with trigger/backfill/scheduler commands | Python 3.11+ · standard library only · uv | Done — all 49 tests pass; CLI E2E (list/trigger/scheduler/backfill) verified end-to-end |
| [`llm-heartbeat-io-mvp/`](llm-heartbeat-io-mvp/) | Byte-level capture of a minimal LLM heartbeat (`ping`) on both sides of the wire: a recording reverse proxy dumps every request/response body, compares the full agent payload against a bare two-field model call, dissects the system prompt by block with CJK-aware token estimates reconciled against the provider's `usage`, and runs a controlled A/B proving the project-context injection cost | Python 3.9+ · standard library only | Done — 3 runs captured (60+ exchanges); one `ping` = 154,340 B / 38,718 prompt tokens vs 90 B / 31 tokens bare (1,249×); cache hit 99.5% on an identical resend |
| [`graphrag-hybrid-mvp/`](graphrag-hybrid-mvp/) | Minimal GraphRAG hybrid retrieval over a Chinese corpus: four channels (self-written BM25, Chinese ONNX embeddings, an LLM-extracted entity/relation graph with alias merging and hop expansion, label-propagation community summaries) fused by weighted RRF plus a per-channel floor, answered by DeepSeek with `[n]` citations; includes a 16-question gold set, a channel-ablation harness (doc-level recall@k / all-gold@k / keyword recall / MRR), content-hash caches for incremental rebuilds, and an offline test suite (ScriptedLLM + hashing embedder) | Python 3.11+ · uv · fastembed (ONNX, no torch) · DeepSeek | Done — 116 offline tests pass; live 22-doc/16-question run: hybrid matches the best single channel on document coverage (all-gold 0.750) and beats BM25 on evidence-keyword recall (0.896 vs 0.854), but does **not** dominate single channels on this small corpus — the measured win is the fusion policy (RRF + per-channel floor recovers a multi-hop chain that plain RRF drops); see `SPEC.md` and `README.md` §验证结果 |
| [`sandbox-center-mvp/`](sandbox-center-mvp/) | The tool-call sandbox chain of a desktop AI agent: Electron main hosts a resident Rust `sandbox-center` (policy authority, session ledger, sha256-chained audit) and spawns a one-shot `sandbox-cli` per tool call, which materialises a Seatbelt profile, runs `/usr/bin/sandbox-exec → zsh → python3/node`, reclaims kernel denials by profile tag from the unified log, and retries once when the center auto-grants | Rust · Electron · Seatbelt (SBPL) | Done — 39 unit + 13 real-kernel e2e tests and an 8-scenario Electron self-test (plus 7 UI assertions and audit-chain verification) all pass; write-escape, delete-protection and network-egress denials are enforced by the kernel and recorded in a tamper-evident audit chain |
| [`supervisor-graph-mvp/`](supervisor-graph-mvp/) | An orchestration layer made of two readable pieces: a lightweight-model Supervisor whose duties stop at intent recognition, DAG planning, routing and aggregation, and an explicit state graph (`intake → classify → plan → dispatch → check` with a repair cycle, a finalize edge and an escalate edge). Four deterministic specialist agents (classification / policy / evidence / remediation) keep the domain answers reproducible while nine consistency rules turn planning defects, missing evidence and fabricated citations into concrete repair targets or an explicit human escalation; ships five scenarios, an offline scripted replay, and a single-file drill-down report of every run | Python 3.9+ · standard library only | Done — 203 offline tests pass; live 5-scenario run on the lightweight `deepseek-flash` model (14 calls / 11,380 tokens) produced allow / review / block / escalate correctly, and the scripted runs exercise the repair cycle, the synthesised-specialist path and the escalation path; see `SPEC.md` |

> **Local-only project.** [`outlook-dlp-mvp/`](outlook-dlp-mvp/) is developed and
> verified on a real machine but is not published here yet: its sources carry
> customer-specific bundle identifiers, log subsystems and test fixtures that
> must be de-identified (and the app re-signed) before they can leave the local
> lab. Everything else in the table is in this repository.

## Repository Structure

```
quest-mvp-lab/
├── es-mvp/                            # Directory-scoped ES AUTH_OPEN (ObjC/Rust/Swift)
├── es-process-mvp/                    # Process-scoped ES denial
├── es-procattr-mvp/                   # ESF process attribution for AI agents
├── es-agent-sensor-mvp/               # NOTIFY-only ES behavior sensor (Rust)
├── outlook-dlp-mvp/                   # Outbound-mail DLP interception (local-only, not published)
├── ai-coding-agent-mvp/               # Minimal agentic coding CLI (Python)
├── react-loop-mvp/                    # Hand-written ReAct loop + native tool-calling contrast
├── agent-guardrails-mvp/              # Five deterministic guardrails around agent tool calls (Python)
├── promptguard-llamafirewall-mvp/     # PromptGuard 2 + LlamaFirewall benchmark
├── promptguard-finetune-mvp/          # PromptGuard 2 fine-tuning (Apple-Silicon MPS)
├── web-design-system-extractor-mvp/   # Evidence-first design-system capture (Playwright)
├── substack-design-system-demo/       # Extracted-token validation console
├── fluid-motion-demo/                 # SwiftUI + Metal motion language demo
├── m7-chart/                          # GPUI Component multi-line chart
├── pdf-inspector-mvp/                 # PDF structure extraction + OCR (Rust)
├── deepseek-v4-flash-vision-exp/      # Vision-model evaluation pipeline
├── rust-appkit-bridge-mvp/            # Rust ↔ AppKit seven-layer bridge
├── airflow-mini-mvp/                  # Toy Airflow re-implementation (Python, stdlib-only)
├── ai-course-engineering-mvp/         # Offline AI-assisted engineering exercises
├── llm-heartbeat-io-mvp/              # Wire-level LLM heartbeat capture + input/output dissection
├── graphrag-hybrid-mvp/               # Four-channel GraphRAG hybrid retrieval + ablation (Python)
├── sandbox-center-mvp/                # Resident sandbox-center + per-call sandbox-cli + Seatbelt enforcement
├── supervisor-graph-mvp/              # Lightweight Supervisor + explicit state graph orchestration (Python)
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
  removed, its table row, its structure-tree line and the Highlights bullet
  above are updated in the same change, and the experiment badge is bumped.
- **Evidence stays local.** Raw captures (`runs/`, `logs/`) and the reports
  built from them — interactive HTML carrying real request/response payloads —
  are never committed; the generator and the static exports are. Clone and
  rebuild beats shipping someone else's traffic.
- **Local-only projects.** A project that still carries machine-specific
  identifiers (bundle IDs, signing identities, internal hostnames) is listed in
  the table with a local-only note and is not linked until it is de-identified.
- **Bundle IDs.** Use the `com.nanzhipro.*` prefix when a bundle identifier is
  required. Keep demo artifacts unsigned unless distribution requires signing.

## Naming

Use `<topic>-mvp` for concept or technology validation and `<topic>-demo` for
showcase examples.