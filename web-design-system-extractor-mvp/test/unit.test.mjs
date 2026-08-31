import assert from "node:assert/strict";
import test from "node:test";

import { parseArgs, parseViewportSpec } from "../src/args.mjs";
import { buildArtifacts, contrastRatio, parseCssColor, parseCssDimension, parsePixelDimension } from "../src/normalize.mjs";
import { renderDesignMarkdown } from "../src/render.mjs";
import { isPrivateAddress, parseHttpUrl, redactUrl } from "../src/url-safety.mjs";
import { validateDesignTokenParity, validateDtcg, validateGoogleDesignMarkdown } from "../src/verify.mjs";

test("URL parsing rejects unsupported schemes and embedded credentials", () => {
    assert.equal(parseHttpUrl("https://example.com/path").hostname, "example.com");
    assert.throws(() => parseHttpUrl("file:///tmp/a"), /Only http/);
    assert.throws(() => parseHttpUrl("https://user:pass@example.com"), /credentials/);
});

test("private address detection covers common IPv4 and IPv6 ranges", () => {
    for (const address of ["127.0.0.1", "10.1.2.3", "172.20.1.2", "192.168.1.2", "169.254.1.1", "::1", "fd00::1"]) {
        assert.equal(isPrivateAddress(address), true, address);
    }
    for (const address of ["1.1.1.1", "8.8.8.8", "2606:4700:4700::1111"]) {
        assert.equal(isPrivateAddress(address), false, address);
    }
});

test("URL redaction preserves routing while removing query values", () => {
    assert.equal(
        redactUrl("https://example.com/a?token=secret&variant=b#section"),
        "https://example.com/a?token=REDACTED&variant=VALUE",
    );
});

test("viewport and CLI argument parsing are deterministic", () => {
    assert.deepEqual(parseViewportSpec("wide:1280x720,phone:390x844"), [
        { name: "wide", width: 1280, height: 720 },
        { name: "phone", width: 390, height: 844 },
    ]);
    assert.throws(() => parseViewportSpec("wide:10x10"), /outside/);
    const parsed = parseArgs(["https://example.com", "--out", "result", "--timeout-ms", "5000"], {
        cwd: "/tmp",
    });
    assert.equal(parsed.outputDir, "/tmp/result");
    assert.equal(parsed.timeoutMs, 5000);
    assert.equal(parsed.viewports.length, 2);
    const strict = parseArgs([
        "https://example.com",
        "--out",
        "result",
        "--require-live-schema",
        "--ignore-robots",
        "--color-scheme",
        "dark",
        "--locale",
        "zh-CN",
        "--timezone",
        "Asia/Shanghai",
        "--reduced-motion",
        "reduce",
    ]);
    assert.equal(strict.requireLiveSchema, true);
    assert.equal(strict.ignoreRobots, true);
    assert.equal(strict.colorScheme, "dark");
    assert.equal(strict.locale, "zh-CN");
    assert.equal(strict.timezoneId, "Asia/Shanghai");
    assert.equal(strict.reducedMotion, "reduce");
});

test("CSS colors and dimensions normalize to stable primitive values", () => {
    assert.deepEqual(parseCssColor("rgb(255, 0, 128)"), { r: 255, g: 0, b: 128, a: 1, hex: "#ff0080" });
    assert.equal(parseCssColor("rgba(0, 0, 0, 0.5)").hex, "#00000080");
    assert.equal(parseCssColor("color(srgb 0.5 0.25 1 / 0.2)").hex, "#8040ff33");
    assert.equal(parseCssColor("lab(97.68 -0.0000298023 0.0000119209)").hex, "#f8f8f8");
    assert.equal(parseCssColor("oklab(0.237169 0.00257336 -0.00914066 / 0.4)").hex, "#1e1e2366");
    assert.equal(parsePixelDimension("-1.5px"), -1.5);
    assert.equal(parsePixelDimension("3.35544e+07px"), 33_554_400);
    assert.equal(parsePixelDimension("1rem"), null);
    assert.deepEqual(parseCssDimension("1.25rem"), { value: 1.25, unit: "rem" });
    assert.deepEqual(parseCssDimension("1e2px"), { value: 100, unit: "px" });
    assert.equal(Number(contrastRatio(parseCssColor("#000"), parseCssColor("#fff")).toFixed(1)), 21);
});

function fixtureCapture(name, width) {
    return {
        viewport: { name, width, height: 800 },
        evidence: {
            metadata: {
                url: "https://example.com/",
                document: { width, height: 1600 },
            },
            coverage: {
                discoveredElements: 12,
                truncated: false,
                visibleElements: 10,
                hiddenElements: 2,
                textElements: 4,
                pseudoElements: 0,
                openShadowRoots: 0,
                closedShadowRoots: "not-observable",
                canvasElements: 0,
                svgElements: 1,
                styleSheets: 1,
                accessibleStyleSheets: 1,
                inaccessibleStyleSheets: 0,
                cssRuleCount: 8,
                frames: 0,
                crossOriginOrUnreadableFrames: 0,
            },
            colors: [
                { value: "rgb(255, 255, 255)", count: 8, roles: { background: 5 }, samples: ["body"] },
                { value: "rgb(17, 17, 17)", count: 6, roles: { text: 6 }, samples: ["p"] },
                { value: "rgb(1, 2, 3)", count: 100, roles: { "decorative-text": 100 }, samples: ["span"] },
            ],
            lengths: [
                { value: "16px", count: 6, roles: { spacing: 4, "font-size": 2 }, samples: ["p"] },
                { value: "8px", count: 2, roles: { radius: 2 }, samples: ["button"] },
                { value: "24px", count: 2, roles: { "line-height": 2 }, samples: ["p"] },
                { value: "0px", count: 2, roles: { "letter-spacing": 2, "border-width": 2 }, samples: ["p"] },
            ],
            typography: [
                {
                    value: JSON.stringify({
                        fontFamily: 'Inter, "Helvetica Neue", sans-serif',
                        fontSize: "16px",
                        fontWeight: "400",
                        lineHeight: "24px",
                        letterSpacing: "0px",
                        textTransform: "none",
                    }),
                    count: 4,
                    roles: { p: 4 },
                    samples: ["p"],
                },
            ],
            shadows: [],
            gradients: [],
            motion: [],
            contrastPairs: [
                {
                    value: JSON.stringify({
                        foreground: "rgb(17, 17, 17)",
                        background: "rgb(255, 255, 255)",
                        backgroundImage: false,
                        fontSize: "16px",
                        fontWeight: "400",
                    }),
                    count: 4,
                    roles: { text: 4 },
                    samples: ["p"],
                },
            ],
            cssVariables: [{ name: "--brand", value: "#ff0080", count: 1, samples: ["inline-style"] }],
            mediaQueries: ["(max-width: 600px)"],
            fonts: [{ family: "Inter", style: "normal", weight: "400", stretch: "normal", status: "loaded" }],
            assets: [{ url: "https://example.com/logo.svg", type: "image", count: 1, samples: ["img"] }],
            componentCandidates: [
                {
                    tag: "button",
                    role: "",
                    text: "Continue",
                    style: {
                        color: "rgb(17, 17, 17)",
                        backgroundColor: "rgb(255, 255, 255)",
                        border: "1px solid rgb(17, 17, 17)",
                        borderRadius: "8px",
                        boxShadow: "none",
                        fontFamily: "Inter, sans-serif",
                        fontSize: "16px",
                        fontWeight: "400",
                        lineHeight: "24px",
                        padding: "8px 16px",
                    },
                },
            ],
        },
    };
}

test("artifact builder emits valid DTCG tokens and safe DESIGN.md", () => {
    const metadata = {
        title: "Example <script>alert(1)</script>",
        sourceUrl: "https://example.com/",
        capturedAt: "2026-08-30T00:00:00.000Z",
    };
    const artifacts = buildArtifacts([fixtureCapture("desktop", 1280), fixtureCapture("mobile", 390)], metadata);
    const validation = validateDtcg(artifacts.tokens);
    assert.equal(validation.passed, true, validation.errors.join("\n"));
    assert.ok(validation.tokenCount >= 8);
    assert.equal(
        artifacts.tokens.$schema,
        "https://www.designtokens.org/schemas/2025.10/format.json",
    );
    assert.match(artifacts.css, /--color-palette-hex-ffffff/);
    assert.equal(artifacts.tokens.color.palette["hex-010203"], undefined);
    const markdown = renderDesignMarkdown({
        raw: artifacts.raw,
        targetUrl: metadata.sourceUrl,
        capturedAt: metadata.capturedAt,
        toolVersion: "test",
    });
    assert.doesNotMatch(markdown, /<script>/i);
    assert.match(markdown, /&lt;script&gt;/);
    assert.match(markdown, /^---\nversion: alpha/m);
    assert.deepEqual(Array.from(markdown.matchAll(/^##\s+(.+)$/gm), (match) => match[1]), [
        "Overview",
        "Colors",
        "Typography",
        "Layout",
        "Elevation & Depth",
        "Shapes",
        "Components",
        "Do's and Don'ts",
    ]);
    const googleValidation = validateGoogleDesignMarkdown(markdown);
    assert.equal(googleValidation.passed, true, googleValidation.errors.join("\n"));
    assert.equal(googleValidation.summary.errors, 0);
    const parity = validateDesignTokenParity(googleValidation.frontmatter, artifacts.tokens);
    assert.equal(parity.passed, true, parity.errors.join("\n"));
});

test("Google DESIGN.md validation rejects warning-only legacy output and wrong section order", () => {
    const legacy = "# Legacy\n\n## Colors\n\nObserved only.\n";
    const validation = validateGoogleDesignMarkdown(legacy);
    assert.equal(validation.passed, false);
    assert.match(validation.errors.join("\n"), /frontmatter/);
    assert.match(validation.errors.join("\n"), /H2 sections/);
});

test("DTCG validator rejects missing types and broken aliases", () => {
    const invalid = {
        color: {
            bad: { $value: "{color.missing}" },
        },
    };
    const validation = validateDtcg(invalid);
    assert.equal(validation.passed, false);
    assert.match(validation.errors.join("\n"), /no explicit or inherited/);
    assert.match(validation.errors.join("\n"), /missing token/);

    const cyclic = {
        color: {
            $type: "color",
            a: { $value: "{color.b}" },
            b: { $value: "{color.a}" },
        },
    };
    const cycleValidation = validateDtcg(cyclic);
    assert.equal(cycleValidation.passed, false);
    assert.match(cycleValidation.errors.join("\n"), /cycle detected/);
});
