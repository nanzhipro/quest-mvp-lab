import assert from "node:assert/strict";
import fs from "node:fs/promises";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { executeExtraction } from "../src/cli.mjs";
import { fileDigest } from "../src/files.mjs";
import { revalidateExtraction } from "../src/revalidate.mjs";

const IMAGE = Buffer.from(
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAFElEQVR4nGP4z8Dwn4GBgYGJAQoAHgQCAf2uPzoAAAAASUVORK5CYII=",
    "base64",
);

const CSS = `
:root {
  --color-canvas: #f7f9fc;
  --color-ink: #172033;
  --color-brand: #0b6bcb;
  --space-card: 24px;
  --radius-card: 12px;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  color: var(--color-ink);
  background: var(--color-canvas);
  font-family: Inter, ui-sans-serif, system-ui, sans-serif;
  font-size: 16px;
  line-height: 24px;
}
main { width: min(100% - 32px, 960px); margin: 0 auto; padding: 48px 0; }
.hero { padding: 48px; border-radius: 16px; background: linear-gradient(135deg, #ffffff, #dcecff); }
h1 { margin: 0 0 16px; font-size: 48px; line-height: 52px; letter-spacing: -1px; }
.card { margin-top: 32px; padding: var(--space-card); background: #ffffff; border: 1px solid #c8d2e3; border-radius: var(--radius-card); box-shadow: 0 8px 24px rgba(23, 32, 51, 0.12); }
.actions { display: flex; gap: 12px; margin-top: 20px; }
button, a.action { min-height: 44px; padding: 10px 18px; border-radius: 8px; border: 1px solid #0b6bcb; font: inherit; font-weight: 600; }
button { color: #ffffff; background: #0b6bcb; transition: background-color 180ms ease; }
a.action { color: #0b6bcb; background: transparent; text-decoration: none; }
.spacer { height: 1100px; }
@media (max-width: 600px) {
  main { width: min(100% - 24px, 960px); padding: 24px 0; }
  .hero { padding: 24px; }
  h1 { font-size: 34px; line-height: 40px; }
  .actions { flex-direction: column; }
}
`;

const HTML = `<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Extractor Fixture</title>
    <link rel="stylesheet" href="/styles.css">
  </head>
  <body>
    <main>
      <section class="hero">
        <h1>Evidence first design extraction</h1>
        <p>Responsive typography, surfaces, motion, assets, and components.</p>
        <div class="actions"><button type="button">Start capture</button><a class="action" href="#details">Details</a></div>
      </section>
      <article id="details" class="card">
        <h2>Reusable surface</h2>
        <p>This card supplies a repeated border, radius, and elevation pattern.</p>
        <fixture-control></fixture-control>
        <canvas width="120" height="40" aria-label="Canvas sample"></canvas>
      </article>
      <div class="spacer" aria-hidden="true"></div>
      <img loading="lazy" src="/pixel.png" width="2" height="2" alt="Lazy fixture pixel">
    </main>
    <script>
      customElements.define('fixture-control', class extends HTMLElement {
        connectedCallback() {
          const root = this.attachShadow({ mode: 'open' });
          root.innerHTML = '<style>button{padding:8px 12px;border-radius:6px;background:#172033;color:#fff}</style><button>Shadow action</button>';
        }
      });
      const context = document.querySelector('canvas').getContext('2d');
      context.fillStyle = '#0b6bcb';
      context.fillRect(0, 0, 120, 40);
    </script>
  </body>
</html>`;

async function startFixtureServer({ robots = "User-agent: *\nAllow: /\n" } = {}) {
    const server = http.createServer((request, response) => {
        if (request.url === "/styles.css") {
            response.writeHead(200, { "content-type": "text/css", "cache-control": "no-store" });
            response.end(CSS);
            return;
        }
        if (request.url === "/pixel.png") {
            response.writeHead(200, { "content-type": "image/png", "content-length": IMAGE.length });
            response.end(IMAGE);
            return;
        }
        if (request.url === "/robots.txt") {
            response.writeHead(200, { "content-type": "text/plain" });
            response.end(robots);
            return;
        }
        response.writeHead(200, { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" });
        response.end(HTML);
    });
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    const address = server.address();
    return {
        url: `http://127.0.0.1:${address.port}/`,
        close: () => new Promise((resolve, reject) => server.close((error) => (error ? reject(error) : resolve()))),
    };
}

test("end-to-end extraction captures responsive evidence and passes validation", { timeout: 120_000 }, async (t) => {
    const fixture = await startFixtureServer();
    const temporaryRoot = await fs.mkdtemp(path.join(os.tmpdir(), "web-ds-extractor-e2e-"));
    const outputDir = path.join(temporaryRoot, "output");
    t.after(async () => {
        await fixture.close();
        await fs.rm(temporaryRoot, { recursive: true, force: true });
    });

    const result = await executeExtraction({
        url: fixture.url,
        outputDir,
        viewports: [
            { name: "desktop", width: 1280, height: 720 },
            { name: "mobile", width: 390, height: 844 },
        ],
        timeoutMs: 20_000,
        colorScheme: "light",
        locale: "en-US",
        timezoneId: "UTC",
        reducedMotion: "no-preference",
        maxElements: 5_000,
        maxResourceBytes: 2 * 1024 * 1024,
        maxTotalBytes: 10 * 1024 * 1024,
        allowPrivate: true,
        ignoreRobots: false,
        requireLiveSchema: false,
        headed: false,
    });

    assert.equal(result.manifest.status, "PASS");
    assert.equal(result.manifest.grade, "complete-for-declared-scope");
    assert.equal(result.validation.passed, true);
    const tokens = JSON.parse(await fs.readFile(path.join(outputDir, "design.tokens.json"), "utf8"));
    assert.ok(tokens.color.palette["hex-0b6bcb"]);
    assert.ok(tokens.dimension.radius["px-12"]);
    const inventory = JSON.parse(await fs.readFile(path.join(outputDir, "raw-inventory.json"), "utf8"));
    assert.ok(inventory.mediaQueries.includes("(max-width: 600px)"));
    assert.ok(inventory.observed.motion.length > 0);
    assert.ok(inventory.viewports.every((viewport) => viewport.coverage.openShadowRoots >= 1));
    assert.ok(result.manifest.captureSummary.every((capture) => capture.mhtml.captured));
    assert.ok(result.manifest.captureSummary.every((capture) => capture.network.archivedResourceCount >= 2));
    assert.ok(result.manifest.captureSummary.every((capture) => capture.settling.animationsStabilized));
    const design = await fs.readFile(path.join(outputDir, "DESIGN.md"), "utf8");
    assert.match(design, /^---\nversion: alpha/m);
    assert.match(design, /## Components/);
    assert.match(design, /### Confidence and Known Limits/);
    assert.equal(result.validation.googleDesignValidation.passed, true);
    assert.equal(result.validation.googleDesignValidation.summary.errors, 0);
    assert.equal(result.validation.designTokenParity.passed, true);
    assert.ok(result.validation.designTokenParity.bindingCount > 0);
    assert.equal(result.validation.cssTokenParity.passed, true);
    assert.equal(result.validation.cssTokenParity.tokenCount, result.validation.cssTokenParity.cssVariableCount);
    assert.equal(result.validation.cssBrowserValidation.passed, true);
    assert.equal(result.validation.cssBrowserValidation.probeCount, result.validation.cssTokenParity.tokenCount);
    const css = await fs.readFile(path.join(outputDir, "tokens.css"), "utf8");
    assert.doesNotMatch(css, /\[object Object\]|""[^"\n]+""/);

    const revalidated = await revalidateExtraction(outputDir);
    assert.equal(revalidated.validation.passed, true);
    const updatedManifest = JSON.parse(await fs.readFile(path.join(outputDir, "manifest.json"), "utf8"));
    const validationRecord = updatedManifest.files.find((file) => file.path === "validation.json");
    assert.ok(validationRecord);
    assert.equal(validationRecord.sha256, (await fileDigest(path.join(outputDir, "validation.json"))).sha256);
});

test("robots.txt denial fails before browser capture unless explicitly ignored", { timeout: 30_000 }, async (t) => {
    const fixture = await startFixtureServer({ robots: "User-agent: *\nDisallow: /\n" });
    const temporaryRoot = await fs.mkdtemp(path.join(os.tmpdir(), "web-ds-extractor-robots-"));
    t.after(async () => {
        await fixture.close();
        await fs.rm(temporaryRoot, { recursive: true, force: true });
    });
    await assert.rejects(
        executeExtraction({
            url: fixture.url,
            outputDir: path.join(temporaryRoot, "denied"),
            viewports: [{ name: "desktop", width: 1280, height: 720 }],
            timeoutMs: 10_000,
            colorScheme: "light",
            locale: "en-US",
            timezoneId: "UTC",
            reducedMotion: "no-preference",
            maxElements: 1_000,
            maxResourceBytes: 1024 * 1024,
            maxTotalBytes: 2 * 1024 * 1024,
            allowPrivate: true,
            ignoreRobots: false,
            requireLiveSchema: false,
            headed: false,
        }),
        /robots\.txt disallows/,
    );
});
