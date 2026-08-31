import fs from "node:fs/promises";
import path from "node:path";
import { lint } from "@google/design.md/linter";
import Ajv from "ajv";
import addFormats from "ajv-formats";
import { PNG } from "pngjs";
import { parse as parseYaml } from "yaml";

import { GOOGLE_DESIGN_SECTION_ORDER } from "./google-design.mjs";
import { parseCssColor, parseCssDimension } from "./normalize.mjs";

const VALID_TYPES = new Set([
    "color",
    "dimension",
    "fontFamily",
    "fontWeight",
    "duration",
    "cubicBezier",
    "number",
    "strokeStyle",
    "border",
    "transition",
    "shadow",
    "gradient",
    "typography",
]);

function checkTokenValue(type, value) {
    if (typeof value === "string" && /^\{[^{}]+\}$/.test(value)) {
        return true;
    }
    switch (type) {
        case "color":
            return (
                value &&
                value.colorSpace === "srgb" &&
                Array.isArray(value.components) &&
                value.components.length === 3 &&
                value.components.every((part) => typeof part === "number" && part >= 0 && part <= 1) &&
                typeof value.alpha === "number" &&
                value.alpha >= 0 &&
                value.alpha <= 1
            );
        case "dimension":
            return value && typeof value.value === "number" && ["px", "rem"].includes(value.unit);
        case "fontFamily":
            return (
                typeof value === "string" ||
                (Array.isArray(value) && value.length > 0 && value.every((part) => typeof part === "string" && part.length > 0))
            );
        case "fontWeight":
            return (
                (typeof value === "number" && value >= 1 && value <= 1000) ||
                ["thin", "hairline", "extra-light", "ultra-light", "light", "normal", "regular", "book", "medium", "semi-bold", "demi-bold", "bold", "extra-bold", "ultra-bold", "black", "heavy", "extra-black", "ultra-black"].includes(value)
            );
        case "number":
            return typeof value === "number" && Number.isFinite(value);
        default:
            return value !== undefined;
    }
}

export function validateDtcg(tokens) {
    const errors = [];
    const tokenPaths = new Set();
    const references = [];
    let tokenCount = 0;

    function visit(node, pathParts, inheritedType) {
        if (!node || typeof node !== "object" || Array.isArray(node)) {
            errors.push(`${pathParts.join(".") || "root"} must be an object`);
            return;
        }
        const type = node.$type ?? inheritedType;
        if (node.$type !== undefined && !VALID_TYPES.has(node.$type)) {
            errors.push(`${pathParts.join(".") || "root"} has unknown $type '${node.$type}'`);
        }
        if (Object.hasOwn(node, "$value")) {
            tokenCount += 1;
            const tokenPath = pathParts.join(".");
            tokenPaths.add(tokenPath);
            if (!type) {
                errors.push(`${tokenPath} has no explicit or inherited $type`);
            } else if (!checkTokenValue(type, node.$value)) {
                errors.push(`${tokenPath} has an invalid ${type} value`);
            }
            if (typeof node.$value === "string" && /^\{[^{}]+\}$/.test(node.$value)) {
                references.push({ source: tokenPath, target: node.$value.slice(1, -1) });
            }
            return;
        }
        for (const [name, child] of Object.entries(node)) {
            if (name.startsWith("$")) {
                continue;
            }
            if (/[.{}]/.test(name)) {
                errors.push(`${[...pathParts, name].join(".")} uses a reserved token-name character`);
            }
            visit(child, [...pathParts, name], type);
        }
    }

    visit(tokens, [], undefined);
    for (const reference of references) {
        if (!tokenPaths.has(reference.target)) {
            errors.push(`${reference.source} references missing token ${reference.target}`);
        }
    }
    const graph = new Map();
    for (const reference of references) {
        const targets = graph.get(reference.source) ?? [];
        targets.push(reference.target);
        graph.set(reference.source, targets);
    }
    const visiting = new Set();
    const visited = new Set();
    const cycles = new Set();
    function visitReference(tokenPath, stack) {
        if (visiting.has(tokenPath)) {
            const start = stack.indexOf(tokenPath);
            cycles.add([...stack.slice(start), tokenPath].join(" -> "));
            return;
        }
        if (visited.has(tokenPath)) {
            return;
        }
        visiting.add(tokenPath);
        stack.push(tokenPath);
        for (const target of graph.get(tokenPath) ?? []) {
            if (tokenPaths.has(target)) {
                visitReference(target, stack);
            }
        }
        stack.pop();
        visiting.delete(tokenPath);
        visited.add(tokenPath);
    }
    for (const tokenPath of graph.keys()) {
        visitReference(tokenPath, []);
    }
    for (const cycle of cycles) {
        errors.push(`Token reference cycle detected: ${cycle}`);
    }
    if (tokenCount === 0) {
        errors.push("No design tokens were produced");
    }
    return { passed: errors.length === 0, tokenCount, referenceCount: references.length, errors };
}

export async function validateOfficialDtcgSchema(tokens, { required = false, timeoutMs = 20_000 } = {}) {
    const schemaUrl = "https://www.designtokens.org/schemas/2025.10/format.json";
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
        const response = await fetch(schemaUrl, { signal: controller.signal });
        if (!response.ok) {
            throw new Error(`schema fetch returned HTTP ${response.status}`);
        }
        const schema = await response.json();
        const ajv = new Ajv({ allErrors: true, strict: false, validateFormats: true });
        addFormats(ajv);
        ajv.addFormat("json-pointer-uri-fragment", {
            type: "string",
            validate: (value) => /^#\/(?:[^/~]|~[01]|\/)*$/.test(value),
        });
        const validate = ajv.compile(schema);
        const valid = validate(tokens);
        return {
            attempted: true,
            required,
            schemaUrl,
            schemaId: schema.$id ?? null,
            passed: Boolean(valid),
            errors: validate.errors ?? [],
        };
    } catch (error) {
        return {
            attempted: true,
            required,
            schemaUrl,
            unavailable: true,
            passed: !required,
            errors: [error instanceof Error ? error.message : String(error)],
        };
    } finally {
        clearTimeout(timer);
    }
}

export async function analyzePng(filePath) {
    const buffer = await fs.readFile(filePath);
    const image = PNG.sync.read(buffer);
    const pixelCount = image.width * image.height;
    const stride = Math.max(1, Math.floor(pixelCount / 120_000));
    const colors = new Set();
    let samples = 0;
    let opaqueSamples = 0;
    let luminanceSum = 0;
    let luminanceSquaredSum = 0;
    for (let pixel = 0; pixel < pixelCount; pixel += stride) {
        const offset = pixel * 4;
        const r = image.data[offset];
        const g = image.data[offset + 1];
        const b = image.data[offset + 2];
        const a = image.data[offset + 3];
        samples += 1;
        if (a > 0) {
            opaqueSamples += 1;
        }
        const luminance = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
        luminanceSum += luminance;
        luminanceSquaredSum += luminance * luminance;
        colors.add(`${r >> 4}:${g >> 4}:${b >> 4}:${a >> 6}`);
    }
    const mean = luminanceSum / samples;
    const variance = Math.max(0, luminanceSquaredSum / samples - mean * mean);
    return {
        width: image.width,
        height: image.height,
        bytes: buffer.length,
        sampledPixels: samples,
        opaqueRatio: opaqueSamples / samples,
        quantizedColorCount: colors.size,
        luminanceVariance: variance,
        nonBlank: opaqueSamples / samples > 0.95 && colors.size >= 8 && variance > 0.00005,
    };
}

export function validateGoogleDesignMarkdown(markdown) {
    const report = lint(markdown);
    const sections = Array.from(markdown.matchAll(/^##\s+(.+?)\s*$/gm), (match) => match[1]);
    const structuralErrors = [];
    const frontmatterMatch = /^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/.exec(markdown);
    let frontmatter = null;
    if (!frontmatterMatch) {
        structuralErrors.push("YAML frontmatter must be the first block in the document");
    } else {
        try {
            frontmatter = parseYaml(frontmatterMatch[1]);
        } catch (error) {
            structuralErrors.push(`YAML frontmatter is invalid: ${error.message}`);
        }
    }
    if (frontmatter && (typeof frontmatter !== "object" || Array.isArray(frontmatter))) {
        structuralErrors.push("YAML frontmatter must be a mapping");
        frontmatter = null;
    }
    if (frontmatter) {
        if (frontmatter.version !== "alpha") {
            structuralErrors.push("YAML frontmatter version must be 'alpha'");
        }
        if (typeof frontmatter.name !== "string" || frontmatter.name.trim().length === 0) {
            structuralErrors.push("YAML frontmatter name must be a non-empty string");
        }
        const omitted = new Set(
            (Array.isArray(frontmatter.omitted) ? frontmatter.omitted : []).map((item) =>
                typeof item === "string" ? item : item?.section,
            ),
        );
        for (const group of ["colors", "typography", "spacing", "rounded", "components"]) {
            const values = frontmatter[group];
            const hasValues = values && typeof values === "object" && !Array.isArray(values) && Object.keys(values).length > 0;
            if (!hasValues && !omitted.has(group)) {
                structuralErrors.push(`YAML frontmatter ${group} must contain values or be declared in omitted`);
            }
        }
        if (!omitted.has("colors") && !frontmatter.colors?.primary) {
            structuralErrors.push("YAML frontmatter colors.primary is required when colors are not omitted");
        }
    }
    if (JSON.stringify(sections) !== JSON.stringify(GOOGLE_DESIGN_SECTION_ORDER)) {
        structuralErrors.push(
            `H2 sections must be exactly ${GOOGLE_DESIGN_SECTION_ORDER.join(" -> ")}; observed ${sections.join(" -> ")}`,
        );
    }
    if (report.summary.errors > 0) {
        structuralErrors.push(
            ...report.findings.filter((finding) => finding.severity === "error").map((finding) => finding.message),
        );
    }
    return {
        passed: structuralErrors.length === 0,
        summary: report.summary,
        findings: report.findings,
        sections,
        frontmatter,
        errors: structuralErrors,
    };
}

function collectPrimitiveTokenValues(tokens) {
    const values = new Map();
    function visit(node, inheritedType) {
        if (!node || typeof node !== "object" || Array.isArray(node)) {
            return;
        }
        const type = node.$type ?? inheritedType;
        if (Object.hasOwn(node, "$value")) {
            if (typeof node.$value !== "string" || !/^\{[^{}]+\}$/.test(node.$value)) {
                const entries = values.get(type) ?? [];
                entries.push(node.$value);
                values.set(type, entries);
            }
            return;
        }
        for (const [key, child] of Object.entries(node)) {
            if (!key.startsWith("$")) {
                visit(child, type);
            }
        }
    }
    visit(tokens, undefined);
    return values;
}

function dtcgColorHex(value) {
    if (!value || value.colorSpace !== "srgb" || !Array.isArray(value.components) || value.components.length !== 3) {
        return null;
    }
    const bytes = value.components.map((component) => Math.round(component * 255));
    const alpha = Math.round((value.alpha ?? 1) * 255);
    const body = bytes.map((byte) => byte.toString(16).padStart(2, "0")).join("");
    return `#${body}${alpha < 255 ? alpha.toString(16).padStart(2, "0") : ""}`;
}

function normalizeFontFamily(value) {
    const parts = Array.isArray(value) ? value : String(value).split(",");
    return parts.map((part) => String(part).trim().replace(/^["']|["']$/g, "").toLowerCase()).filter(Boolean).join("|");
}

export function validateDesignTokenParity(frontmatter, tokens) {
    const errors = [];
    const values = collectPrimitiveTokenValues(tokens);
    const colors = new Set((values.get("color") ?? []).map(dtcgColorHex).filter(Boolean));
    const dimensions = new Set(
        (values.get("dimension") ?? []).map((value) =>
            value && typeof value.value === "number" && value.unit ? `${value.value}${value.unit}` : null,
        ).filter(Boolean),
    );
    const fontFamilies = new Set((values.get("fontFamily") ?? []).map(normalizeFontFamily));
    const fontWeights = new Set(values.get("fontWeight") ?? []);

    for (const [name, value] of Object.entries(frontmatter?.colors ?? {})) {
        const parsed = parseCssColor(value);
        if (!parsed || !colors.has(parsed.hex)) {
            errors.push(`colors.${name}=${value} has no observed DTCG color primitive`);
        }
    }
    for (const group of ["spacing", "rounded"]) {
        for (const [name, value] of Object.entries(frontmatter?.[group] ?? {})) {
            const parsed = parseCssDimension(value);
            const normalized = parsed ? `${parsed.value}${parsed.unit}` : null;
            if (!normalized || !dimensions.has(normalized)) {
                errors.push(`${group}.${name}=${value} has no observed DTCG dimension primitive`);
            }
        }
    }
    for (const [name, typography] of Object.entries(frontmatter?.typography ?? {})) {
        if (!fontFamilies.has(normalizeFontFamily(typography.fontFamily))) {
            errors.push(`typography.${name}.fontFamily has no observed DTCG font-family primitive`);
        }
        if (typography.fontWeight !== undefined && !fontWeights.has(Number(typography.fontWeight))) {
            errors.push(`typography.${name}.fontWeight has no observed DTCG font-weight primitive`);
        }
        for (const property of ["fontSize", "lineHeight", "letterSpacing"]) {
            const value = typography[property];
            if (value === undefined || typeof value === "number") {
                continue;
            }
            const parsed = parseCssDimension(value);
            const normalized = parsed ? `${parsed.value}${parsed.unit}` : null;
            if (!normalized || !dimensions.has(normalized)) {
                errors.push(`typography.${name}.${property}=${value} has no observed DTCG dimension primitive`);
            }
        }
    }
    return { passed: errors.length === 0, errors };
}

async function fileExists(filePath) {
    try {
        const stat = await fs.stat(filePath);
        return stat.isFile() && stat.size > 0;
    } catch {
        return false;
    }
}

export async function verifyExtraction(outputDir, captures, tokens, { requireLiveSchema = false } = {}) {
    const checks = [];
    const warnings = [];
    function add(name, passed, detail) {
        checks.push({ name, passed, detail });
    }

    const tokenValidation = validateDtcg(tokens);
    add(
        "DTCG token structure",
        tokenValidation.passed,
        tokenValidation.passed
            ? `${tokenValidation.tokenCount} tokens and ${tokenValidation.referenceCount} references validated`
            : tokenValidation.errors.join("; "),
    );

    let officialDtcgSchema = { attempted: false, required: requireLiveSchema, passed: true, errors: [] };
    if (requireLiveSchema) {
        officialDtcgSchema = await validateOfficialDtcgSchema(tokens, { required: true });
        add(
            "DTCG 2025.10 official live JSON Schema",
            officialDtcgSchema.passed,
            officialDtcgSchema.passed
                ? `${officialDtcgSchema.schemaId} validated with 0 errors`
                : officialDtcgSchema.errors.map((error) => error.message ?? error).join("; "),
        );
    }

    const designMarkdown = await fs.readFile(path.join(outputDir, "DESIGN.md"), "utf8");
    const googleDesignValidation = validateGoogleDesignMarkdown(designMarkdown);
    add(
        "Google DESIGN.md 0.4.0 lint and structure",
        googleDesignValidation.passed,
        googleDesignValidation.passed
            ? `${googleDesignValidation.summary.errors} errors, ${googleDesignValidation.summary.warnings} warnings, ${googleDesignValidation.summary.infos} infos; all 8 canonical H2 sections present once and in order`
            : googleDesignValidation.errors.join("; "),
    );
    for (const finding of googleDesignValidation.findings.filter((item) => item.severity === "warning")) {
        warnings.push(`Google DESIGN.md: ${finding.message}`);
    }
    const designTokenParity = validateDesignTokenParity(googleDesignValidation.frontmatter, tokens);
    add(
        "DESIGN.md and DTCG observed-value parity",
        designTokenParity.passed,
        designTokenParity.passed ? "all normative Google token values map to observed DTCG primitives" : designTokenParity.errors.join("; "),
    );

    for (const capture of captures) {
        const name = capture.viewport.name;
        const evidenceDir = path.join(outputDir, "evidence", name);
        add(
            `${name} rendered evidence`,
            capture.evidence.coverage.visibleElements > 0 && !capture.evidence.coverage.truncated,
            `${capture.evidence.coverage.visibleElements} visible of ${capture.evidence.coverage.discoveredElements} discovered elements; truncated=${capture.evidence.coverage.truncated}`,
        );
        add(
            `${name} DOM archive`,
            await fileExists(path.join(evidenceDir, "rendered-dom.html")),
            "rendered-dom.html exists and is non-empty",
        );
        add(
            `${name} MHTML snapshot`,
            capture.mhtml.captured && (await fileExists(path.join(evidenceDir, "page.mhtml"))),
            capture.mhtml.captured ? `${capture.mhtml.bytes} bytes` : capture.mhtml.error || "capture failed",
        );
        add(
            `${name} HAR metadata`,
            await fileExists(path.join(evidenceDir, "network.har")),
            `${capture.network.totalResourceCount} responses, ${capture.network.failedRequestCount} failed requests`,
        );
        for (const screenshotFile of capture.screenshot.files) {
            const stats = await analyzePng(path.join(evidenceDir, screenshotFile));
            add(
                `${name} ${screenshotFile} pixels`,
                stats.nonBlank && stats.width >= Math.min(240, capture.viewport.width) && stats.height >= 240,
                `${stats.width}x${stats.height}, ${stats.quantizedColorCount} quantized colors, variance=${stats.luminanceVariance.toFixed(6)}`,
            );
        }
        if (!capture.settling.networkIdleReached) {
            warnings.push(`${name}: networkidle was not reached; bounded font, scroll, DOM, and screenshot gates still ran.`);
        }
        if (!capture.settling.fontsReady) {
            warnings.push(`${name}: document.fonts readiness was not reached; inspect the captured FontFaceSet evidence.`);
        }
        if (!capture.settling.imagesDecoded) {
            warnings.push(`${name}: one or more image readiness checks timed out; inspect the screenshot and resources.json.`);
        }
        if (!capture.settling.autoScroll?.stable) {
            warnings.push(`${name}: document height did not stabilize before the scroll budget expired.`);
        }
        if (capture.settling.autoScroll?.finalHeight !== capture.evidence.metadata.document.height) {
            warnings.push(
                `${name}: document height changed from ${capture.settling.autoScroll?.finalHeight} to ${capture.evidence.metadata.document.height} after scrolling; late content may require a repeat run.`,
            );
        }
        if (capture.network.failedRequestCount > 0) {
            warnings.push(`${name}: ${capture.network.failedRequestCount} network requests failed; inspect resources.json.`);
        }
        if (capture.network.blockedRequestCount > 0) {
            warnings.push(`${name}: ${capture.network.blockedRequestCount} unsafe network requests were blocked.`);
        }
        if (capture.evidence.coverage.inaccessibleStyleSheets > 0) {
            warnings.push(
                `${name}: ${capture.evidence.coverage.inaccessibleStyleSheets} stylesheet(s) rendered but CSSOM rules were not readable.`,
            );
        }
        if (capture.evidence.coverage.crossOriginOrUnreadableFrames > 0) {
            warnings.push(
                `${name}: ${capture.evidence.coverage.crossOriginOrUnreadableFrames} iframe(s) were cross-origin or unreadable.`,
            );
        }
        if (capture.evidence.coverage.canvasElements > 0) {
            warnings.push(`${name}: ${capture.evidence.coverage.canvasElements} canvas element(s) are screenshot-only evidence.`);
        }
    }

    const requiredFiles = ["DESIGN.md", "design.tokens.json", "tokens.css", "raw-inventory.json"];
    for (const filename of requiredFiles) {
        add(`Output ${filename}`, await fileExists(path.join(outputDir, filename)), "file exists and is non-empty");
    }
    return {
        passed: checks.every((check) => check.passed),
        validatedAt: new Date().toISOString(),
        checks,
        warnings,
        tokenValidation,
        officialDtcgSchema,
        googleDesignValidation,
        designTokenParity,
    };
}
