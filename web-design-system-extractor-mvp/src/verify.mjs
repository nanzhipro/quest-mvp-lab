import fs from "node:fs/promises";
import path from "node:path";
import { lint } from "@google/design.md/linter";
import Ajv from "ajv";
import addFormats from "ajv-formats";
import { chromium } from "playwright";
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

function collectDtcgTokenEntries(tokens) {
    const entries = new Map();
    function visit(node, pathParts = [], inheritedType) {
        if (!node || typeof node !== "object" || Array.isArray(node)) {
            return;
        }
        const type = node.$type ?? inheritedType;
        if (Object.hasOwn(node, "$value")) {
            const tokenPath = pathParts.join(".");
            entries.set(tokenPath, { path: tokenPath, type, value: node.$value });
            return;
        }
        for (const [key, child] of Object.entries(node)) {
            if (!key.startsWith("$")) {
                visit(child, [...pathParts, key], type);
            }
        }
    }
    visit(tokens);
    return entries;
}

function resolveDtcgToken(entries, tokenPath, seen = new Set()) {
    if (seen.has(tokenPath)) {
        return null;
    }
    seen.add(tokenPath);
    const entry = entries.get(tokenPath);
    if (!entry) {
        return null;
    }
    if (typeof entry.value === "string" && /^\{[^{}]+\}$/.test(entry.value)) {
        return resolveDtcgToken(entries, entry.value.slice(1, -1), seen);
    }
    return entry;
}

function cssVariableName(tokenPath) {
    return `--${tokenPath
        .split(".")
        .map((part) => part.replace(/([a-z0-9])([A-Z])/g, "$1-$2").toLowerCase())
        .join("-")}`;
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
    return parts
        .map((part) => String(part).trim().replace(/^(["'])(.*)\1$/, "$2").trim().toLowerCase())
        .filter(Boolean)
        .join("|");
}

export function validateDesignTokenParity(frontmatter, tokens) {
    const errors = [];
    const bindings = [];
    const references = [];
    const entries = collectDtcgTokenEntries(tokens);

    function equivalent(type, observed, canonical) {
        if (type === "color") {
            return parseCssColor(observed)?.hex === dtcgColorHex(canonical);
        }
        if (type === "dimension") {
            const parsed = parseCssDimension(observed);
            return Boolean(
                parsed && canonical && parsed.value === canonical.value && parsed.unit === canonical.unit,
            );
        }
        if (type === "fontFamily") {
            return normalizeFontFamily(observed) === normalizeFontFamily(canonical);
        }
        if (type === "fontWeight") {
            return Number(observed) === Number(canonical);
        }
        return false;
    }

    function bind(designPath, observed, type, prefixes) {
        const match = Array.from(entries.values()).find((entry) => {
            if (entry.type !== type || !prefixes.some((prefix) => entry.path.startsWith(prefix))) {
                return false;
            }
            const resolved = resolveDtcgToken(entries, entry.path);
            return resolved && equivalent(type, observed, resolved.value);
        });
        if (!match) {
            errors.push(`${designPath}=${observed} has no matching ${type} token in ${prefixes.join(" or ")}`);
            return;
        }
        bindings.push({ designPath, dtcgPath: match.path, cssVariable: cssVariableName(match.path) });
    }

    for (const [name, value] of Object.entries(frontmatter?.colors ?? {})) {
        bind(`colors.${name}`, value, "color", ["color.semantic.", "source.color.", "color.palette."]);
    }
    for (const [name, value] of Object.entries(frontmatter?.spacing ?? {})) {
        bind(`spacing.${name}`, value, "dimension", ["dimension.spacing."]);
    }
    for (const [name, value] of Object.entries(frontmatter?.rounded ?? {})) {
        bind(`rounded.${name}`, value, "dimension", ["dimension.radius."]);
    }
    for (const [name, typography] of Object.entries(frontmatter?.typography ?? {})) {
        bind(`typography.${name}.fontFamily`, typography.fontFamily, "fontFamily", ["typography.fontFamily."]);
        if (typography.fontWeight !== undefined) {
            bind(`typography.${name}.fontWeight`, typography.fontWeight, "fontWeight", ["typography.fontWeight."]);
        }
        for (const [property, prefix] of [
            ["fontSize", "dimension.fontSize."],
            ["lineHeight", "dimension.lineHeight."],
            ["letterSpacing", "dimension.letterSpacing."],
        ]) {
            const value = typography[property];
            if (value === undefined) {
                continue;
            }
            bind(`typography.${name}.${property}`, value, "dimension", [prefix]);
        }
    }

    for (const [componentName, component] of Object.entries(frontmatter?.components ?? {})) {
        for (const [property, value] of Object.entries(component ?? {})) {
            if (typeof value !== "string" || !/^\{[^{}]+\}$/.test(value)) {
                continue;
            }
            const target = value.slice(1, -1);
            const parts = target.split(".");
            let resolved = frontmatter;
            for (const part of parts) {
                resolved = resolved?.[part];
            }
            if (resolved === undefined) {
                errors.push(`components.${componentName}.${property} references missing DESIGN.md value ${target}`);
            } else {
                references.push({ designPath: `components.${componentName}.${property}`, target });
            }
        }
    }
    return { passed: errors.length === 0, bindingCount: bindings.length, bindings, references, errors };
}

function parseCssCustomProperties(css) {
    const declarations = new Map();
    const errors = [];
    for (const line of css.split(/\r?\n/)) {
        const match = /^\s*(--[a-zA-Z0-9_-]+)\s*:\s*(.*?)\s*;\s*$/.exec(line);
        if (!match) {
            continue;
        }
        if (declarations.has(match[1])) {
            errors.push(`Duplicate CSS custom property ${match[1]}`);
        }
        declarations.set(match[1], match[2]);
    }
    return { declarations, errors };
}

function parseCssFontFamily(value) {
    const parts = [];
    let current = "";
    let quote = null;
    let escaped = false;
    for (const character of value) {
        if (quote) {
            if (escaped) {
                current += character;
                escaped = false;
            } else if (character === "\\") {
                escaped = true;
            } else if (character === quote) {
                quote = null;
            } else {
                current += character;
            }
            continue;
        }
        if (character === '"' || character === "'") {
            if (current.trim().length > 0) {
                return null;
            }
            quote = character;
        } else if (character === ",") {
            const part = current.trim();
            if (!part) {
                return null;
            }
            parts.push(part);
            current = "";
        } else {
            current += character;
        }
    }
    const finalPart = current.trim();
    if (quote || escaped || !finalPart) {
        return null;
    }
    parts.push(finalPart);
    return parts;
}

function cssString(value) {
    return `"${value.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`;
}

function validationCssValue(type, value) {
    if (type === "color") {
        if (!value || value.colorSpace !== "srgb" || !Array.isArray(value.components) || value.components.length !== 3) {
            return null;
        }
        return `color(srgb ${value.components.join(" ")} / ${value.alpha ?? 1})`;
    }
    if (type === "dimension" && value && typeof value.value === "number" && value.unit) {
        return `${value.value}${value.unit}`;
    }
    if (type === "fontFamily") {
        const parts = Array.isArray(value) ? value : [value];
        if (parts.length === 0 || parts.some((part) => typeof part !== "string" || part.trim().length === 0)) {
            return null;
        }
        return parts
            .map((part) => part.trim().replace(/^(["'])(.*)\1$/, "$2").trim())
            .map((part) => (/\s/.test(part) ? cssString(part) : part))
            .join(", ");
    }
    if (type === "fontWeight" && (typeof value === "number" || typeof value === "string")) {
        return String(value);
    }
    return null;
}

export function validateCssTokenParity(tokens, css) {
    const entries = collectDtcgTokenEntries(tokens);
    const { declarations, errors } = parseCssCustomProperties(css);
    const expectedNames = new Set();

    for (const entry of entries.values()) {
        const cssName = cssVariableName(entry.path);
        expectedNames.add(cssName);
        const actual = declarations.get(cssName);
        if (actual === undefined) {
            errors.push(`${entry.path} is missing CSS custom property ${cssName}`);
            continue;
        }
        if (/\[object Object\]|\b(?:undefined|NaN)\b/.test(actual)) {
            errors.push(`${cssName} contains a non-serializable value: ${actual}`);
            continue;
        }
        if (typeof entry.value === "string" && /^\{[^{}]+\}$/.test(entry.value)) {
            const expected = `var(${cssVariableName(entry.value.slice(1, -1))})`;
            if (actual !== expected) {
                errors.push(`${cssName}=${actual} does not preserve DTCG alias ${expected}`);
            }
            continue;
        }
        if (entry.type === "color") {
            if (parseCssColor(actual)?.hex !== dtcgColorHex(entry.value)) {
                errors.push(`${cssName}=${actual} does not equal its DTCG color value`);
            }
        } else if (entry.type === "dimension") {
            const parsed = parseCssDimension(actual);
            if (!parsed || parsed.value !== entry.value?.value || parsed.unit !== entry.value?.unit) {
                errors.push(`${cssName}=${actual} does not equal its DTCG dimension value`);
            }
        } else if (entry.type === "fontFamily") {
            const parsed = parseCssFontFamily(actual);
            if (!parsed || normalizeFontFamily(parsed) !== normalizeFontFamily(entry.value)) {
                errors.push(`${cssName}=${actual} does not equal its DTCG fontFamily value`);
            }
        } else if (entry.type === "fontWeight") {
            if (String(entry.value) !== actual) {
                errors.push(`${cssName}=${actual} does not equal its DTCG fontWeight value`);
            }
        } else {
            errors.push(`${entry.path} uses unsupported CSS output type ${entry.type ?? "unknown"}`);
        }
    }

    for (const name of declarations.keys()) {
        if (!expectedNames.has(name)) {
            errors.push(`CSS custom property ${name} has no matching DTCG token`);
        }
    }

    return {
        passed: errors.length === 0,
        tokenCount: entries.size,
        cssVariableCount: declarations.size,
        errors,
    };
}

export async function validateCssBrowserConsumption(tokens, css) {
    const entries = collectDtcgTokenEntries(tokens);
    const errors = [];
    const probes = [];
    for (const entry of entries.values()) {
        const resolved = resolveDtcgToken(entries, entry.path);
        if (!resolved) {
            errors.push(`${entry.path} could not be resolved for browser validation`);
            continue;
        }
        const expected = validationCssValue(entry.type, resolved.value);
        const property = {
            color: "color",
            dimension: "margin-left",
            fontFamily: "font-family",
            fontWeight: "font-weight",
        }[entry.type];
        if (!property || expected === null) {
            errors.push(`${entry.path} cannot be represented in a browser consumption probe`);
            continue;
        }
        probes.push({
            tokenPath: entry.path,
            type: entry.type,
            cssVariable: cssVariableName(entry.path),
            property,
            expected,
        });
    }

    let browser;
    try {
        browser = await chromium.launch({ headless: true });
        const page = await browser.newPage();
        await page.setContent("<!doctype html><html><head></head><body></body></html>");
        await page.addStyleTag({ content: css });
        const results = await page.evaluate((items) => {
            const container = document.createElement("div");
            container.style.cssText = "position:absolute;left:-100000px;top:0";
            document.body.append(container);
            return items.map((item) => {
                const actual = document.createElement("div");
                const expected = document.createElement("div");
                actual.style.setProperty(item.property, `var(${item.cssVariable})`);
                expected.style.setProperty(item.property, item.expected);
                container.append(actual, expected);
                return {
                    tokenPath: item.tokenPath,
                    type: item.type,
                    actual: getComputedStyle(actual).getPropertyValue(item.property).trim(),
                    expected: getComputedStyle(expected).getPropertyValue(item.property).trim(),
                };
            });
        }, probes);
        for (const result of results) {
            const matches =
                result.type === "color"
                    ? parseCssColor(result.actual)?.hex === parseCssColor(result.expected)?.hex
                    : result.actual === result.expected;
            if (!matches) {
                errors.push(`${result.tokenPath} computed as '${result.actual}', expected '${result.expected}'`);
            }
        }
    } catch (error) {
        errors.push(`Chromium CSS validation failed: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
        await browser?.close();
    }

    return { passed: errors.length === 0, probeCount: probes.length, errors };
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
        "DESIGN.md to DTCG typed-path bindings",
        designTokenParity.passed,
        designTokenParity.passed
            ? `${designTokenParity.bindingCount} normative values bound to typed DTCG paths; ${designTokenParity.references.length} component references resolved`
            : designTokenParity.errors.join("; "),
    );

    const css = await fs.readFile(path.join(outputDir, "tokens.css"), "utf8");
    const cssTokenParity = validateCssTokenParity(tokens, css);
    add(
        "DTCG to CSS structural and value parity",
        cssTokenParity.passed,
        cssTokenParity.passed
            ? `${cssTokenParity.tokenCount} DTCG tokens map one-to-one to ${cssTokenParity.cssVariableCount} CSS custom properties`
            : cssTokenParity.errors.slice(0, 12).join("; "),
    );
    const cssBrowserValidation = cssTokenParity.passed
        ? await validateCssBrowserConsumption(tokens, css)
        : { passed: false, probeCount: 0, errors: ["Skipped because DTCG to CSS parity failed"] };
    add(
        "CSS custom properties consumed by Chromium",
        cssBrowserValidation.passed,
        cssBrowserValidation.passed
            ? `${cssBrowserValidation.probeCount} token values and aliases matched direct DTCG-derived computed styles`
            : cssBrowserValidation.errors.slice(0, 12).join("; "),
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
        cssTokenParity,
        cssBrowserValidation,
    };
}
