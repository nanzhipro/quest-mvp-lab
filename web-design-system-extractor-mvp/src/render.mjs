import { parseCssColor, parsePixelDimension } from "./normalize.mjs";
import { buildGoogleDesignSystem, collectActiveVariables, renderGoogleFrontmatter } from "./google-design.mjs";

function safeText(value, maximumLength = 240) {
    return String(value ?? "")
        .replace(/[\u0000-\u001f\u007f]/g, " ")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/\|/g, "\\|")
        .replace(/`/g, "'")
        .replace(/\s+/g, " ")
        .trim()
        .slice(0, maximumLength);
}

function roleSummary(roles) {
    return Object.entries(roles ?? {})
        .sort((left, right) => right[1] - left[1])
        .slice(0, 3)
        .map(([role, count]) => `${role}:${count}`)
        .join(", ");
}

function luminance(color) {
    if (!color) {
        return 0.5;
    }
    return (0.2126 * color.r + 0.7152 * color.g + 0.0722 * color.b) / 255;
}

function inferVisualDirection(raw) {
    const background = raw.observed.colors.find((item) => (item.roles.background ?? 0) > 0);
    const parsedBackground = parseCssColor(background?.value);
    const theme = luminance(parsedBackground) < 0.42 ? "dark-dominant" : "light-dominant";
    const radii = raw.observed.lengths
        .filter((item) => (item.roles.radius ?? 0) > 0)
        .map((item) => parsePixelDimension(item.value))
        .filter((value) => value !== null);
    const medianRadius = radii.length > 0 ? [...radii].sort((a, b) => a - b)[Math.floor(radii.length / 2)] : 0;
    const motionOccurrences = raw.observed.motion.reduce((sum, item) => sum + item.count, 0);
    return {
        theme,
        shape: medianRadius >= 12 ? "strongly rounded" : medianRadius >= 4 ? "moderately rounded" : "mostly square",
        motion: motionOccurrences > 50 ? "motion-rich" : motionOccurrences > 0 ? "selective motion" : "mostly static",
        confidence: "inferred from computed styles; confirm against screenshots before reuse",
    };
}

function tableRows(records, mapper, emptyMessage = "No observable values") {
    if (records.length === 0) {
        return `| ${emptyMessage} | - | - |\n| --- | --- | --- |`;
    }
    return records.map(mapper).join("\n");
}

function parseTypography(item) {
    try {
        return JSON.parse(item.value);
    } catch {
        return null;
    }
}

function displayRadius(value) {
    const numeric = Number.parseFloat(value);
    if (Number.isFinite(numeric) && numeric >= 1_000_000) {
        return `${value} (full/pill radius)`;
    }
    return value;
}

export function renderDesignMarkdown({ raw, tokens, targetUrl, capturedAt, toolVersion }) {
    const title = safeText(raw.metadata.title || "Extracted Web Design System", 120).replace(/\\\|/g, "|");
    const direction = inferVisualDirection(raw);
    const colors = raw.observed.colors
        .map((item) => ({ ...item, parsed: parseCssColor(item.value) }))
        .filter((item) => item.parsed && item.parsed.a > 0)
        .filter((item, index, array) => array.findIndex((candidate) => candidate.parsed.hex === item.parsed.hex) === index)
        .sort((left, right) => {
            const meaningful = (item) =>
                Object.entries(item.roles ?? {})
                    .filter(([role]) => role !== "decorative-text")
                    .reduce((sum, [, count]) => sum + count, 0);
            return meaningful(right) - meaningful(left) || right.count - left.count;
        })
        .slice(0, 24);
    const typographyCandidates = raw.observed.typography
        .map((item) => ({ ...item, parsed: parseTypography(item) }))
        .filter((item) => item.parsed);
    const typography = typographyCandidates.slice(0, 12);
    for (const role of ["h1", "h2", "h3", "h4", "button", "a"]) {
        const candidate = typographyCandidates.find((item) => (item.roles[role] ?? 0) > 0);
        if (candidate && !typography.some((item) => item.value === candidate.value)) {
            typography.push(candidate);
        }
    }
    const spacing = raw.observed.lengths.filter((item) => (item.roles.spacing ?? 0) > 0).slice(0, 24);
    const radii = raw.observed.lengths.filter((item) => (item.roles.radius ?? 0) > 0).slice(0, 16);
    const components = raw.componentPatterns.slice(0, 24);
    const failedContrast = raw.accessibility.audited.filter((item) => !item.pass).slice(0, 12);
    const variableCount = raw.cssVariables.reduce((sum, viewport) => sum + viewport.values.length, 0);
    const allActiveVariables = collectActiveVariables(raw);
    const activeVariables = allActiveVariables
        .filter((item) =>
            /(?:background|foreground|canvas|surface|content|text|border|primary|secondary|accent|muted|gray|grey|radius|space|gap)/i.test(
                item.name,
            ),
        )
        .filter((item) => !/^--(?:tw|radix|framer|motion)-/i.test(item.name))
        .slice(0, 40);
    const googleDesignSystem = buildGoogleDesignSystem(raw, title, tokens);
    const frontmatter = renderGoogleFrontmatter(googleDesignSystem);

    const lines = [
        frontmatter,
        "",
        `# ${title} - Extracted Design System`,
        "",
        "> Google DESIGN.md alpha-compatible, evidence-first reconstruction of a rendered public web page. Frontmatter values are normative for this extracted artifact; inferred semantics remain capture-scoped.",
        "",
        "## Overview",
        "",
        `The captured surface is **${direction.theme}**, uses **${direction.shape}** geometry, and is **${direction.motion}**. This statement is ${direction.confidence}.`,
        "",
        "Use this document as the agent-facing design specification, `design.tokens.json` as the source of truth for exact token values, and the screenshots as the final visual authority. `tokens.css` is a validated compilation of the DTCG tokens; `raw-inventory.json` preserves provenance.",
        "",
        "### Capture Contract",
        "",
        `- **Source:** ${safeText(targetUrl, 500)}`,
        `- **Captured:** ${capturedAt}`,
        `- **Extractor:** web-design-system-extractor ${toolVersion}`,
        `- **Viewports:** ${raw.viewports.map((viewport) => `${viewport.name} ${viewport.width}x${viewport.height}`).join(", ")}`,
        "- **Evidence:** rendered DOM, computed styles, CSSOM where readable, MHTML, HAR metadata, visual-resource hashes, and full-page or tiled screenshots.",
        "- **Scope:** one URL and its fully rendered state at the declared viewports. This is not a claim that every authenticated, interactive, personalized, or time-varying state was captured.",
        "",
        "### Confidence and Known Limits",
        "",
        "| Area | Confidence | Reason |",
        "| --- | --- | --- |",
        "| Computed colors, dimensions, typography | High for captured states | Read from rendered elements in Chromium |",
        "| Public CSS variables and media rules | High where CSSOM was readable | Cross-origin CSSOM may be blocked even when the stylesheet rendered |",
        "| Semantic token names | Medium to low | Inferred from source-variable names and rendered roles |",
        "| Component boundaries | Medium to low | Clustered from rendered tags and styles; framework ownership is not observable |",
        "| Canvas/WebGL pixels | Screenshot only | Internal scene graphs and shader parameters are not recoverable from DOM/CSSOM |",
        "| Closed shadow roots and cross-origin iframe internals | Not fully observable | Browser security and encapsulation boundaries apply |",
        "| Hover/focus/active/authenticated/personalized states | Not captured unless separately scripted | One URL can expose multiple UI states |",
        "",
        "A PASS means the declared evidence and validators succeeded. It does not mean the private design source, author naming, or every runtime state was reconstructed.",
        "",
        "## Colors",
        "",
        "| Observed color | Uses | Main roles | Token confidence |",
        "| --- | ---: | --- | --- |",
        ...colors.map(
            (item) =>
                `| \`${item.parsed.hex}\` | ${item.count} | ${safeText(roleSummary(item.roles))} | observed |`,
        ),
        "",
        "The `color.semantic` tokens alias active source variables such as `--background`, `--foreground`, and `--border` when they exist; otherwise they fall back to rendered-role inference. Every token records its confidence and provenance under `$extensions`.",
        "",
        "### Active Source Variables",
        "",
        "The `source` token group preserves active, parseable root custom properties with their original names in `$extensions`. These have higher naming confidence than frequency-derived palette tokens, but they still reflect only the captured theme and cascade.",
        "",
        "| Custom property | Active computed value |",
        "| --- | --- |",
        ...activeVariables.map((item) => `| \`${safeText(item.name)}\` | \`${safeText(item.value, 300)}\` |`),
        "",
        "### Accessibility Evidence",
        "",
        `Opaque text/background pairs: ${raw.accessibility.passingOccurrences} passing occurrences, ${raw.accessibility.failingOccurrences} failing occurrences, ${raw.accessibility.unauditableOccurrences} occurrences not safely auditable because of alpha, imagery, gradients, or unavailable context, and ${raw.accessibility.likelyDecorativeOccurrences} single-glyph span occurrences separated as likely decorative.`,
        "",
        "The decorative classification is a review aid, not an accessibility exemption. Confirm `aria-hidden`, accessible names, and intended meaning in source before excluding those nodes from WCAG review.",
        "",
        ...(failedContrast.length > 0
            ? [
                  "| Foreground | Background | Ratio | Threshold | Uses |",
                  "| --- | --- | ---: | ---: | ---: |",
                  ...failedContrast.map(
                      (item) =>
                          `| \`${item.foreground}\` | \`${item.background}\` | ${item.ratio}:1 | ${item.threshold}:1 | ${item.observedCount} |`,
                  ),
              ]
            : ["No failing opaque contrast pair was observed. This does not replace an interaction-state audit."]),
        "",
        "## Typography",
        "",
        "| Font stack | Size / line height | Weight | Letter spacing | Uses |",
        "| --- | --- | ---: | --- | ---: |",
        ...typography.map((item) => {
            const style = item.parsed;
            return `| ${safeText(style.fontFamily)} | \`${safeText(style.fontSize)} / ${safeText(style.lineHeight)}\` | ${safeText(style.fontWeight)} | \`${safeText(style.letterSpacing)}\` | ${item.count} |`;
        }),
        "",
        "Font files and `FontFaceSet` status are preserved in the evidence inventory. A reported family does not grant redistribution rights for the underlying font binary.",
        "",
        "## Layout",
        "",
        "The machine-readable spacing scale contains only selected rendered pixel values. The table below retains the broader observed distribution so implementation choices remain auditable.",
        "",
        "### Observed Spacing",
        "",
        "| Computed value | Uses | Viewports |",
        "| --- | ---: | --- |",
        ...spacing.map((item) => `| \`${safeText(item.value)}\` | ${item.count} | ${item.viewports.join(", ")} |`),
        "",
        "### Responsive Layout",
        "",
        "| Viewport | Document size | Visible elements | CSSOM coverage | Screenshot |",
        "| --- | --- | ---: | --- | --- |",
        ...raw.viewports.map((viewport) => {
            const coverage = viewport.coverage;
            return `| ${viewport.name} ${viewport.width}x${viewport.height} | ${viewport.document.width}x${viewport.document.height} | ${coverage.visibleElements} | ${coverage.accessibleStyleSheets}/${coverage.styleSheets} stylesheets | evidence/${viewport.name}/ |`;
        }),
        "",
        `Observed media/container/support conditions: ${raw.mediaQueries.length}. Review them in \`raw-inventory.json\`; a condition's presence does not prove it matched every captured viewport.`,
        "",
        "## Elevation & Depth",
        "",
        `- **Observed shadows:** ${raw.observed.shadows.length} distinct computed values.`,
        `- **Observed gradients:** ${raw.observed.gradients.length} distinct computed values.`,
        `- **Observed custom-property declarations:** ${variableCount} viewport-specific declarations; ${allActiveVariables.length} active root values in the primary viewport.`,
        "",
        ...raw.observed.shadows.slice(0, 8).map((item) => `- Shadow \`${safeText(item.value, 360)}\` (${item.count} uses)`),
        ...raw.observed.gradients.slice(0, 6).map((item) => `- Gradient \`${safeText(item.value, 360)}\` (${item.count} uses)`),
        "",
        "### Motion",
        "",
        `The rendered page exposed ${raw.observed.motion.length} distinct transition/animation tuples. Motion is sampled first; animation is then stabilized before colors, typography, geometry, components, and screenshots are extracted.`,
        "",
        ...raw.observed.motion.slice(0, 8).map((item) => `- \`${safeText(item.value, 420)}\` (${item.count} uses)`),
        "",
        "## Shapes",
        "",
        "The rounded token scale is selected from rendered corner radii. Extremely large computed values are preserved as the observed pill/full treatment rather than rewritten to an invented constant.",
        "",
        "| Computed value | Uses | Viewports |",
        "| --- | ---: | --- |",
        ...radii.map((item) => `| \`${safeText(displayRadius(item.value))}\` | ${item.count} | ${item.viewports.join(", ")} |`),
        "",
        "Only pixel-resolved computed dimensions become DTCG dimension tokens. Relative source units, `calc()`, container units, and source-variable names stay in the raw inventory so the generator does not fabricate conversions.",
        "",
        "## Components",
        "",
        "These are repeated rendered-style clusters, not recovered source components. Confirm names, variants, states, and ownership against source code when available.",
        "",
        "| Candidate | Repetitions | Key geometry | Example text | Viewports |",
        "| --- | ---: | --- | --- | --- |",
        ...components.map((component) => {
            const candidate = component.role ? `${component.tag}[role=${component.role}]` : component.tag;
            const geometry = `${component.style.padding}; radius ${displayRadius(component.style.borderRadius)}`;
            return `| \`${safeText(candidate)}\` | ${component.count} | ${safeText(geometry)} | ${safeText(component.samples.join(" / "), 120)} | ${component.viewports.join(", ")} |`;
        }),
        "",
        "### Assets",
        "",
        `The rendered DOM referenced ${raw.assets.length} distinct visual asset URLs. Public visual responses are archived within configured size budgets and recorded by SHA-256; scripts and analytics bodies are intentionally not mirrored.`,
        "",
        ...raw.assets.slice(0, 20).map((asset) => `- **${safeText(asset.type)}:** ${safeText(asset.url, 500)}`),
        "",
        "## Do's and Don'ts",
        "",
        "- **Do** start from semantic intent and alias to observed palette primitives; frequency rank is not a durable semantic name.",
        "- **Do** re-check desktop and mobile screenshots after every token or component change.",
        "- **Do** preserve type scale, spacing rhythm, surface contrast, and component-state distinctions together.",
        "- **Don't** add hover, focus, active, disabled, validation, loading, or reduced-motion states until they are observed or explicitly specified.",
        "- **Don't** redistribute captured fonts or images until their licenses and usage rights are confirmed.",
        "- **Don't** treat a successful capture as proof that authenticated, personalized, canvas-internal, or time-varying states were reconstructed.",
        "",
    ];
    return lines.join("\n");
}

export function renderValidationMarkdown(validation) {
    const lines = [
        "# Extraction Validation",
        "",
        `**Result:** ${validation.passed ? "PASS" : "FAIL"}`,
        "",
        `Validated at ${validation.validatedAt}.`,
        "",
        "| Check | Result | Detail |",
        "| --- | --- | --- |",
        ...validation.checks.map(
            (check) => `| ${safeText(check.name)} | ${check.passed ? "PASS" : "FAIL"} | ${safeText(check.detail, 500)} |`,
        ),
        "",
    ];
    if (validation.warnings.length > 0) {
        lines.push("## Warnings", "", ...validation.warnings.map((warning) => `- ${safeText(warning, 600)}`), "");
    }
    return lines.join("\n");
}
