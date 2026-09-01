# Firecrawl Homepage Replica Demo

A static, offline-renderable replica of the [Firecrawl](https://www.firecrawl.dev/)
homepage, rebuilt **entirely from local capture evidence** produced by the
sibling `web-design-system-extractor-mvp` project — no network access at build
or view time.

## Background

`web-design-system-extractor-mvp` captures a rendered page and preserves
auditable evidence: the serialized rendered DOM, archived visual resources
(CSS chunks, fonts, images), HAR metadata, and full-page screenshots. This
demo answers the follow-up question: **is that evidence sufficient to
reconstruct the page offline?**

The capture used here is
`web-design-system-extractor-mvp/artifacts/firecrawl-dev-2026-08-31-release/`
(status `PASS`, grade `complete-for-declared-scope`, desktop `1440x16770`).

## How It Works

`bin/build.mjs` (Node.js, zero dependencies) performs a purely local transform:

1. Copies the 57 archived assets into `dist/assets/`.
2. Rewrites `url(...)` references inside the archived CSS chunks (fonts,
   mask images) to the local hashed filenames.
3. Processes the captured rendered DOM:
   - strips every `<script>` (Next.js runtime, hydration data, analytics);
   - removes third-party tracking-beacon `<img>` tags (absolute non-Firecrawl
     `src`, e.g. `t.co` / `analytics.twitter.com` pixels carrying captured
     session fingerprints), so the replica makes **zero outbound requests**;
   - rewrites `src` / `href` / `poster` / `srcset` and inline `url(...)`
     references that resolve to an archived asset;
   - leaves everything else untouched (in-page anchors keep working, outbound
     links keep pointing at firecrawl.dev).

The URL → file mapping is exact, not heuristic: the extractor names each
archived resource `sha256(full URL)[0:24] + original extension`, so the build
recomputes the digest for every URL found in the DOM and CSS and matches it
against the archived files. This also covers `_next/image?...&w=N` thumbnail
URLs whose query strings are redacted in `resources.json`.

## Build and Run

```bash
node bin/build.mjs            # → dist/index.html + dist/assets/
python3 -m http.server 8791 --directory dist
# open http://localhost:8791/index.html
```

Optional arguments: `node bin/build.mjs <artifact-dir> <out-dir>`.

## Verification

Rendered with Playwright (Chromium, viewport 1440x900). In the verification
harness only, `loading="lazy"` images are promoted to eager so a full-page
screenshot can be taken deterministically; the built artifact is not modified.

- Document height: `1440x16770` — identical to the captured original.
- 80 DOM URL rewrites plus 14 `_next/image` width-variant fallbacks; the only
  remaining 404s are the assets the capture never archived (listed in the
  build output).
- Side-by-side region comparison against `evidence/desktop/screenshot.png`:
  header, hero, logo cloud, feature sections, testimonials, CTA, FAQ, and
  footer match the original layout, typography, and imagery.

## Known Differences (all expected)

- **Canvas decorations are blank.** The page draws its dotted flame/map ASCII
  art and hero particle effects into `<canvas>` at runtime via JavaScript;
  with scripts stripped, those pixels simply don't exist. The extractor
  itself flags canvas content as "screenshot only".
- **No interactivity.** Tabs, accordions, marquees, and the hero demo keep
  their captured static state.
- **A few images were never archived** (e.g. some logo-cloud variants and
  `hero-crawl-lines.png`, listed in the build output). Browsers skip missing
  `<source>` variants in `<picture>` elements, so most gaps are invisible;
  the rest render as empty boxes.

## Licensing Note

The Firecrawl name, logo, fonts, images, and page content remain the property
of their owner. The captured assets are used here solely for local design
system validation; `dist/` is gitignored and must not be published or
redistributed.
