#!/usr/bin/env node
/**
 * build.mjs — rebuild a static, offline-renderable replica of the Firecrawl
 * homepage from web-design-system-extractor capture evidence.
 *
 * Usage:
 *   node bin/build.mjs [artifact-dir] [out-dir]
 *
 * Defaults:
 *   artifact-dir = ../web-design-system-extractor-mvp/artifacts/firecrawl-dev-2026-08-31-release
 *   out-dir      = dist
 *
 * What it does:
 *   1. Copies the archived visual assets (CSS, fonts, images) into <out>/assets.
 *   2. Rewrites url(...) references inside the archived CSS to local hashed names.
 *   3. Processes the captured rendered DOM:
 *      - strips every <script> (Next.js runtime + analytics),
 *      - rewrites src/href/srcset and inline url(...) references that resolve to
 *        an archived asset (archive filename = sha256(full URL)[0:24] + ext),
 *      - falls back to a same-source archived variant for unarchived
 *        `_next/image?...&w=N` width variants,
 *      - neutralizes remaining cross-references (links keep their href but are
 *        inert without JS; missing images 404 harmlessly).
 *   4. Writes <out>/index.html.
 *
 * Nothing is fetched from the network; the build is purely a local transform of
 * the extractor evidence. Redistribution note: the assets remain Firecrawl's
 * copyrighted material — keep the output local, do not publish it.
 */
import { createHash } from 'node:crypto';
import {
  copyFileSync,
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { basename, dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const PROJECT = resolve(HERE, '..');
const DEFAULT_ARTIFACTS = resolve(
  PROJECT,
  '../web-design-system-extractor-mvp/artifacts/firecrawl-dev-2026-08-31-release'
);

const artifactDir = resolve(process.argv[2] ?? DEFAULT_ARTIFACTS);
const outDir = resolve(process.argv[3] ?? join(PROJECT, 'dist'));
const ORIGIN = 'https://www.firecrawl.dev';

const evidenceDir = join(artifactDir, 'evidence', 'desktop');
const assetsDir = join(evidenceDir, 'assets');
const domPath = join(evidenceDir, 'rendered-dom.html');

for (const p of [assetsDir, domPath]) {
  if (!existsSync(p)) {
    console.error(`missing evidence input: ${p}`);
    process.exit(1);
  }
}

const digest = (url) => createHash('sha256').update(url).digest('hex').slice(0, 24);

// --- 1. copy assets ----------------------------------------------------------
rmSync(outDir, { recursive: true, force: true });
mkdirSync(join(outDir, 'assets'), { recursive: true });

const archived = readdirSync(assetsDir);
for (const file of archived) {
  copyFileSync(join(assetsDir, file), join(outDir, 'assets', file));
}

// --- 2. rewrite archived CSS internals ---------------------------------------
// Font/image URLs inside the CSS chunks point at /_next/static/media/... or
// /assets-original/...; rewrite the ones that were archived.
const cssFiles = archived.filter((f) => f.endsWith('.css'));
let cssRewrites = 0;
for (const file of cssFiles) {
  const path = join(outDir, 'assets', file);
  let css = readFileSync(path, 'utf8');
  css = css.replace(/url\((?!["']?data:)([^)]+)\)/g, (match, raw) => {
    const ref = raw.trim().replace(/^["']|["']$/g, '');
    const absolute = new URL(ref, `${ORIGIN}/_next/static/chunks/`).href;
    const hit = archived.find((a) => a.startsWith(digest(absolute)));
    if (!hit) return match;
    cssRewrites += 1;
    return `url("${hit}")`;
  });
  writeFileSync(path, css);
}

// --- 3. process the rendered DOM ---------------------------------------------
let html = readFileSync(domPath, 'utf8');

// 3a. strip all scripts (Next.js runtime, hydration data, analytics).
html = html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '');
html = html.replace(/<script\b[^>]*\/>/gi, '');

// 3a2. drop third-party beacon/tracking <img> tags (absolute non-firecrawl
// src). They carry captured session fingerprints and would otherwise phone
// home from the supposedly offline replica.
let beacons = 0;
html = html.replace(/<img\b[^>]*>/gi, (tag) => {
  const m = tag.match(/src="(https?:\/\/[^"]+)"/i);
  if (m && !m[1].replace(/&amp;/g, '&').startsWith(ORIGIN)) {
    beacons += 1;
    return '';
  }
  return tag;
});

// Resolve one captured URL to its archived filename, if it exists.
const archivedByDigest = new Map(archived.map((f) => [f.split('.')[0], f]));
const resolveArchived = (url) => archivedByDigest.get(digest(url)) ?? null;

// Precompute: map each archived `_next/image` file to its source `url=` query
// parameter, so unarchived width variants can fall back to the archived
// variant of the same source image.
const collectUrls = (text) => {
  const urls = new Set();
  const push = (u) => {
    if (!u || u.startsWith('data:')) return;
    const abs = u.startsWith('/') ? ORIGIN + u : u;
    if (abs.startsWith(ORIGIN)) urls.add(abs.replace(/&amp;/g, '&'));
  };
  for (const m of text.matchAll(/(?:src|href|poster)="([^"]+)"/g)) push(m[1]);
  for (const m of text.matchAll(/srcset="([^"]+)"/g)) {
    for (const part of m[1].split(',')) push(part.trim().split(/\s+/)[0]);
  }
  return [...urls];
};

const domUrls = collectUrls(html);
const nextImageSources = new Map(); // source param -> archived file
for (const u of domUrls) {
  if (!u.includes('/_next/image?')) continue;
  const hit = resolveArchived(u);
  if (!hit) continue;
  nextImageSources.set(new URL(u).searchParams.get('url'), hit);
}

const stats = { rewritten: 0, fallback: 0, missing: new Set() };
const rewriteUrl = (u) => {
  // Attribute values are HTML-escaped (&amp;); digests are computed over the
  // real URL, so unescape before matching.
  const raw = u.replace(/&amp;/g, '&');
  const abs = raw.startsWith('/') ? ORIGIN + raw : raw;
  if (!abs.startsWith(ORIGIN)) return u;
  const hit = resolveArchived(abs);
  if (hit) {
    stats.rewritten += 1;
    return `assets/${hit}`;
  }
  if (abs.includes('/_next/image?')) {
    const source = new URL(abs).searchParams.get('url');
    const variant = nextImageSources.get(source);
    if (variant) {
      stats.fallback += 1;
      return `assets/${variant}`;
    }
  }
  if (/\.(png|jpe?g|webp|avif|gif|svg|woff2?|css)(\?|$)/.test(abs)) {
    stats.missing.add(abs);
  }
  return u;
};

// 3b. rewrite src/href/poster attributes.
html = html.replace(/(src|href|poster)="([^"]+)"/g, (m, attr, u) => {
  if (u.startsWith('data:') || u.startsWith('#')) return m;
  return `${attr}="${rewriteUrl(u)}"`;
});
// 3c. rewrite srcset candidates.
html = html.replace(/srcset="([^"]+)"/g, (m, list) => {
  const out = list
    .split(',')
    .map((part) => {
      const [u, ...desc] = part.trim().split(/\s+/);
      return [rewriteUrl(u), ...desc].join(' ');
    })
    .join(', ');
  return `srcset="${out}"`;
});
// 3d. rewrite url(...) inside inline <style> blocks and style attributes.
// Quotes may appear HTML-escaped as &quot; inside style="..." attributes.
html = html.replace(/url\((?!["']?data:)(["']|&quot;)?(\/[^)'"&]+)\1\)/g, (m, _q, u) => {
  return `url("${rewriteUrl(u)}")`;
});

// --- 4. write output ----------------------------------------------------------
writeFileSync(join(outDir, 'index.html'), html);

console.log(`replica written to ${outDir}`);
console.log(`  assets copied:      ${archived.length}`);
console.log(`  css url() rewrites: ${cssRewrites}`);
console.log(`  dom url rewrites:   ${stats.rewritten}`);
console.log(`  tracking beacons removed: ${beacons}`);
console.log(`  _next/image fallback width variants: ${stats.fallback}`);
if (stats.missing.size) {
  console.log(`  referenced but not archived (${stats.missing.size}):`);
  for (const u of stats.missing) console.log(`    - ${u.slice(0, 140)}`);
}
