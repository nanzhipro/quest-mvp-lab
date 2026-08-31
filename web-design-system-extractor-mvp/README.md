# Web Design System Extractor MVP

An evidence-first command-line tool that renders a single authorized webpage at
specified viewport sizes, preserves auditable capture evidence, and produces
two reusable design system artifacts:

- a Google Labs `DESIGN.md` alpha document, validated with
  `@google/design.md` 0.4.0;
- DTCG 2025.10 JSON design tokens with an official `$schema` declaration,
  plus equivalent CSS custom properties.

The current version is `0.3.0`. Every run uses the built-in DTCG validator. For
stricter release and CI workflows, you can also require validation against the
official live JSON Schema.

This MVP demonstrates that a rendered page can be converted into an auditable
design system without claiming that a single capture can reconstruct the
original design source or every possible runtime state. Its definition of
completion is intentionally narrow:
**complete for the declared capture profile**.

## What This Validates

Most page-to-design-system tools stop at a screenshot, scrape raw CSS, or emit
tokens without provenance. This project validates a more rigorous pipeline:

1. Validate the target URL and honor its `robots.txt` policy by default.
2. Render the page in real Chromium contexts at multiple viewport sizes.
3. Apply time-bounded readiness checks and scroll through lazy-loaded regions.
4. Preserve the rendered DOM, MHTML, sanitized network traffic, fetched
   resources, and final pixel output.
5. Inspect visible computed styles, accessible CSSOM, open Shadow DOM, pseudo-
   elements, motion, typography, geometry, assets, and contrast pairs.
6. Normalize observations into deterministic token groups with provenance.
7. Generate `DESIGN.md`, then fail the run if the document, tokens, capture
   coverage, archives, or screenshots do not pass validation.
8. Revalidate saved output without loading the live page again.

### Capture Scope

Each run captures one URL in a fresh, anonymous Chromium context for every
requested viewport, subject to the configured time and resource limits. The
default profiles are desktop `1440x900` and mobile `390x844`.

The manifest records a reproducibility fingerprint that includes the Node,
Playwright, and Chromium versions; viewport dimensions; locale; time zone;
color scheme; reduced-motion preference; device scale factor; and service
worker policy. The evidence for each viewport also records the user agent,
languages, time zone, and media preferences observed by the page.

It does not claim to cover:

- every route in a site;
- authenticated pages, personalized or geolocation-specific content, A/B test
  variants, or real-time data;
- hover, focus, active, modal, or other interaction states that were not
  already visible during the run;
- unbounded feeds or content that loads after the configured scroll or time
  budget;
- internals of closed Shadow DOM, cross-origin iframes, canvas, or WebGL;
- the design author's original token names, component ownership, or intent.

Treat the output as a reconstruction of the captured page state. When inferred
semantics are uncertain, the screenshots and raw evidence remain authoritative.

## Architecture

```text
URL + capture profile
        |
        v
HTTP(S), DNS, redirect, and subresource safety checks
        |
        v
robots.txt fetch and default enforcement
        |
        v
Fresh Chromium context per viewport
  |-- bounded network/font/image readiness
  |-- repeated top-to-bottom lazy-load scroll
  |-- pre-freeze motion sampling
  |-- rendered DOM + MHTML + sanitized HAR + hashed resources
  `-- animation-stabilized computed styles + full-page/tiled screenshot
        |
        v
DOM/CSSOM probe
  |-- visible elements, pseudo elements, open Shadow DOM
  |-- colors, dimensions, typography, shadows, gradients, motion
  |-- CSS variables, media queries, fonts, assets, component candidates
  `-- observable WCAG contrast pairs
        |
        v
Normalization and provenance
        |
        +--> DESIGN.md (Google Labs alpha structure)
        +--> design.tokens.json (DTCG 2025.10 + $schema)
        +--> tokens.css
        +--> raw-inventory.json
        `--> manifest.json (capture fingerprint + file hashes)
        |
        v
Built-in structural/evidence validation + optional official live schema gate
```

The implementation combines four complementary sources of evidence:

| Layer | What it contributes |
| --- | --- |
| Playwright | Browser lifecycle, contexts, navigation, network events, HAR, and screenshots. |
| Rendered DOM and computed style | The computed visual state of visible elements, including elements inside open Shadow DOM. |
| Chromium CDP | MHTML page snapshots through `Page.captureSnapshot`. |
| Pixel evidence | Full-page or tiled PNGs checked for dimensions, opacity, color diversity, and luminance variance. |

`networkidle` is treated as a diagnostic signal, not the sole readiness
criterion. Font loading, bounded scrolling, image decoding, DOM extraction,
and screenshots still proceed when long-lived connections prevent the page
from reaching network idle.

Motion is sampled before stabilization. The extractor then disables animation,
transition, and caret timing before measuring visual tokens or taking
screenshots. This prevents transient animation frames from being promoted to
reusable tokens. Colors found only on likely single-glyph decorative spans are
retained in the raw evidence but excluded from the token palette.

## Requirements

- Node.js 20 or newer
- npm
- Network access to the authorized target and its visual assets
- Chromium installed through Playwright

Install the locked dependencies and browser:

```bash
npm ci
npm run setup
```

## Run

You must provide an output directory that does not already exist. This prevents
a new capture from being mixed with or silently overwriting earlier evidence.

```bash
npm run extract -- https://www.respan.ai/ \
  --out ./artifacts/respan-ai
```

Override the default capture profile as needed:

```bash
npm run extract -- https://example.com/ \
  --out ./artifacts/example-wide-phone \
  --viewports wide:1600x1000,phone:390x844 \
  --timeout-ms 60000 \
  --max-elements 30000
```

Key options:

| Option | Default | Purpose |
| --- | ---: | --- |
| `--viewports` | `desktop:1440x900,mobile:390x844` | Comma-separated `name:WIDTHxHEIGHT` profiles. |
| `--timeout-ms` | `45000` | Navigation and bounded readiness budget. |
| `--color-scheme` | `light` | Browser `light` or `dark` color-scheme preference. |
| `--locale` | `en-US` | Browser locale recorded in the capture fingerprint. |
| `--timezone` | `UTC` | IANA timezone used by the browser context. |
| `--reduced-motion` | `no-preference` | Browser motion preference: `no-preference` or `reduce`. |
| `--max-elements` | `20000` | Maximum DOM/open-Shadow-DOM elements inspected per viewport. |
| `--max-resource-bytes` | `10485760` | Maximum archived bytes for one visual resource. |
| `--max-total-bytes` | `104857600` | Maximum archived visual-resource bytes per viewport. |
| `--require-live-schema` | off | Require a successful fetch and validation against the official DTCG 2025.10 JSON Schema. |
| `--ignore-robots` | off | Bypass a robots denial only when explicit authorization permits it. The decision is still recorded. |
| `--headed` | off | Show Chromium while capturing. |
| `--allow-private` | off | Permit localhost/private destinations for explicitly authorized tests. |

Run `npm run extract -- --help` for the complete accepted ranges.

Use the live-schema DTCG gate when a release or CI policy requires validation
against the official live schema in addition to the always-on local validator:

```bash
npm run extract -- https://example.com/ \
  --out ./artifacts/example-strict \
  --require-live-schema
```

`--ignore-robots` is neither a retry mechanism nor a stealth option. Use it
only when the target owner has explicitly authorized the override.

## Output

```text
<output>/
|-- DESIGN.md
|-- design.tokens.json
|-- tokens.css
|-- raw-inventory.json
|-- VALIDATION.md
|-- validation.json
|-- manifest.json
`-- evidence/
    |-- robots.txt
    |-- robots.json
    `-- <viewport>/
        |-- rendered-dom.html
        |-- page.mhtml
        |-- network.har
        |-- resources.json
        |-- visual-evidence.json
        |-- screenshot.png or screenshot-tile-*.png
        `-- assets/
```

| Artifact | Purpose |
| --- | --- |
| `DESIGN.md` | A compact, agent-readable design system description with the eight canonical sections and alpha frontmatter. |
| `design.tokens.json` | DTCG 2025.10 `$schema`, typed color/dimension/typography/source-variable tokens, aliases, and provenance extensions. |
| `tokens.css` | CSS custom properties generated from the DTCG token hierarchy. |
| `raw-inventory.json` | Merged observations, per-viewport coverage, media queries, fonts, assets, component patterns, and contrast evidence. |
| `VALIDATION.md` / `validation.json` | Human-readable and machine-readable checks, including non-fatal warnings. |
| `manifest.json` | Run status and grade, robots decision, runtime capture fingerprint, summaries, checksums, sizes, and the full output file inventory. |
| `evidence/` | Raw evidence for review. Keep it separate from the smaller, shareable design artifacts when retention or access policies differ. |

If a run fails after creating the output directory, `run-error.json` records
the failure. The CLI exits successfully only when every blocking validation
check passes. Warnings remain visible for operator review.

## Verification

Run the complete local regression suite:

```bash
npm run verify
```

The current suite includes eight unit tests and two end-to-end tests. The main
E2E test starts a local responsive fixture and exercises CSS, motion, lazy
loading, an open Shadow Root, canvas detection, resource archiving, MHTML,
screenshots, DTCG generation, Google `DESIGN.md` validation, and offline
revalidation. The second E2E test verifies that a `robots.txt` denial stops the
run before Chromium starts.

Run a narrower check when needed:

```bash
npm run test:unit
npm run test:e2e
npm run design:lint -- ./artifacts/respan-ai/DESIGN.md
npm run validate -- ./artifacts/respan-ai
npm run validate -- ./artifacts/respan-ai --require-live-schema
```

`web-ds-validate` rebuilds the validation state from an existing
`manifest.json`, `design.tokens.json`, and the visual evidence for each
viewport. It does not revisit the target. It rewrites `validation.json`,
`VALIDATION.md`, the manifest status and grade, `revalidatedAt`, and the
manifest file inventory so that recorded file sizes and SHA-256 hashes match
the updated reports.

The built-in `DESIGN.md` gate is intentionally stricter than the upstream
linter's exit code. It requires valid frontmatter, `version: alpha`, a
non-empty name, required or explicitly omitted token groups,
`colors.primary` whenever colors are present, and all eight canonical H2
sections exactly once and in the required order.

The DTCG validator resolves inherited token types, validates supported
primitive values, rejects reserved characters in token names, verifies every
alias target, and detects alias cycles. Generated token documents declare the
official DTCG 2025.10 schema URL. With `--require-live-schema`, the extractor
also fetches that schema and validates the output with Ajv; either a fetch
failure or a schema violation fails the run. Screenshot checks reject blank or
implausibly small images.

## Respan Validation Baseline

The gitignored local directory `artifacts/respan-ai-2026-08-31-release/`
contains the artifacts from the v0.3.0 black-box validation of the packaged
Agent Skill against `https://www.respan.ai/`:

| Result | Observed value |
| --- | --- |
| Overall validation | PASS, grade `complete-for-declared-scope`, 18/18 checks, 6 warnings |
| Capture fingerprint | Node v22.22.3, Playwright 1.62.1, Chromium 151.0.7922.34, UTC, DPR 1 |
| Robots policy | HTTP 200, allowed for `CodexWebDesignExtractor/0.3`, not ignored |
| Official DTCG 2025.10 Schema | PASS, 0 errors |
| Google `DESIGN.md` 0.4.0 gate | 0 errors, 0 warnings, 1 informational finding |
| `DESIGN.md` / DTCG parity | PASS; all normative Google token values map to observed DTCG primitives |
| DTCG output | 197 tokens, 3 valid references |
| Desktop document | `1440x9787`; 12,004 visible of 12,819 discovered elements; not truncated |
| Mobile document | `390x13527`; 1,918 visible of 2,881 discovered elements; not truncated |
| Page snapshots | Desktop MHTML 4,823,593 bytes; mobile MHTML 3,442,803 bytes |
| Network evidence | 109 visual resources archived; 516 responses, 16 failed requests, and 0 unsafe requests blocked |
| Evidence volume | 130 files, approximately 24 MiB including `manifest.json` |

The six warnings consist of an image-readiness timeout, eight failed network
requests, and one unreadable cross-origin iframe in each profile. They remain
explicit diagnostics rather than being folded into the success result. Both
profiles reached `networkidle`, all bounded checks completed, and every schema,
parity, archive, token, DOM coverage, and pixel check passed.

This baseline demonstrates that the approach works on a long, responsive
production page. It is not a permanent golden snapshot: page content and
third-party requests may change between runs.

## Security and Trust Boundaries

Use this tool only for public pages or targets that you are explicitly
authorized to capture.

- Only `http:` and `https:` URLs are accepted; URLs containing embedded
  credentials are rejected.
- In public-target mode, the extractor resolves the initial URL, redirects,
  and HTTP(S) subresources, then blocks private, loopback, link-local, and other
  supported non-routable address ranges. `--allow-private` relaxes this guard
  and is intended only for authorized local or private testing.
- TLS certificate errors remain fatal. Service workers are blocked, and every
  viewport uses a fresh context with no authenticated browser profile.
- Query parameter values are redacted from structured URL records and generated
  metadata. The sanitized HAR also removes request and response cookies,
  credential-bearing headers, and request bodies.
- Archived visual resources use URL-derived filenames, include SHA-256 body
  digests in their metadata, and are subject to per-resource and per-viewport
  size limits.
- `robots.txt` is parsed for `CodexWebDesignExtractor/0.3` and enforced before
  Chromium starts. By default, an explicit denial, an HTTP 401/403 response, or
  an HTTP 5xx response blocks capture; HTTP 404/410 is treated as no policy
  file. A fetch or parsing failure is recorded as `check-failed` and currently
  fails open. `--ignore-robots` bypasses a denial only when the operator has
  explicit authorization.
- Permission under `robots.txt` is a crawling convention, not proof of
  authorization.
- Strings copied from the page into `DESIGN.md` are escaped and length-limited,
  but all page content is still treated as untrusted input.

Redaction does not guarantee that raw evidence is free of sensitive data. The
rendered DOM, MHTML, screenshots, console messages, and downloaded visual
resources may still contain anything visible to the browser. Review and
protect `evidence/` before retaining or sharing it. When only the reusable
visual system is needed, handle the generated design description and tokens
separately from the raw evidence.

## Known Limits

- CSSOM rules may be inaccessible even when the browser successfully applies a
  cross-origin stylesheet.
- Closed Shadow Roots and documents inside cross-origin iframes cannot be
  inspected.
- Canvas and WebGL internals are visible only in screenshots; the extractor
  cannot recover scene graphs or shader parameters.
- Component detection groups observable tags, roles, text, and styles. It
  cannot identify framework boundaries or original source component names.
- Contrast checks are reliable only for opaque, computed foreground and
  background pairs. Alpha transparency, gradients, images, and complex
  compositing remain outside the auditable surface.
- Fresh browser contexts intentionally exclude signed-in state. This MVP does
  not include scripts for driving additional interaction states.
- The optional live DTCG schema gate depends on the availability and current
  contents of `www.designtokens.org`. The local structural validator remains
  the deterministic baseline.
- The bounded scroll loop may still miss user-triggered, delayed, or
  effectively infinite content. Review `truncated`, settling diagnostics,
  network failures, screenshots, and warnings before accepting a run.

## Long-Term Maintenance

Treat the browser runtime, the Google Labs alpha format, and the live target as
three independent sources of drift.

1. Keep `package-lock.json` as the reproducible dependency baseline. Playwright
   and `@google/design.md` are pinned explicitly in `package.json`.
2. Upgrade the browser runtime and design format dependencies separately. After
   each upgrade, run `npm run verify` before accepting output from a live page.
3. After validator-only changes, revalidate retained evidence with
   `npm run validate -- <output>`. Use `--require-live-schema` for strict
   release gates.
4. Re-run a known authorized public page into a new dated directory. Never
   reuse an earlier output directory.
5. Before comparing artifacts, confirm that the manifest capture fingerprints
   are equivalent. Then compare `validation.json`, `DESIGN.md`, token counts and
   references, warnings, resource failures, screenshot dimensions, and file
   hashes. A visual review is still required whenever the rendered page changes.
6. Add a fixture-backed regression test before adding a new observable state,
   CSS value type, token type, or browser-capture path.
7. Review upstream changes to the `DESIGN.md` alpha format and DTCG schema
   before modifying the renderers or validators. Retain the stricter local
   structure checks even when the upstream linter reports a missing field as a
   warning rather than an error.
8. Separate raw-evidence retention from generated-token retention, and expire
   old capture bodies according to the target's authorization and data policy.

`manifest.json` is the stable audit contract for automation. Publish or consume
only runs whose status is `PASS`, and apply an explicit warning policy suited
to the target.

## Project Layout

```text
bin/web-ds-extract.mjs  capture CLI entry point
bin/web-ds-validate.mjs retained-output validation CLI
src/args.mjs            argument and viewport contract
src/url-safety.mjs      URL parsing, DNS policy, and redaction
src/capture.mjs         Chromium capture and evidence archival
src/files.mjs           file inventory, byte counts, and SHA-256 hashes
src/page-probe.mjs      in-page rendered-style probe
src/normalize.mjs       DTCG tokens, provenance, contrast, and CSS output
src/google-design.mjs   Google Labs alpha frontmatter construction
src/render.mjs          DESIGN.md and validation report rendering
src/verify.mjs          local/live DTCG, DESIGN.md, archive, and PNG gates
src/revalidate.mjs      retained-output validation and manifest refresh
test/                   unit and local E2E coverage
```

## Conclusion

This MVP provides an end-to-end, auditable path from a URL to capture evidence,
a design system, design tokens, and machine-verifiable results. Version 0.3.0
adds default `robots.txt` enforcement, a strict official-schema mode, a
reproducible runtime fingerprint, and independent revalidation of retained
evidence. The Respan run demonstrates the approach on a real responsive page,
while the capture contract and explicit warnings keep the result precise: it
reconstructs what Chromium observed under a declared profile, not a private
design source or every state in an application.
