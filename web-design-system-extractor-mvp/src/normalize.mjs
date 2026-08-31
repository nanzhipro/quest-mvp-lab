import { converter } from "culori";

const EXTENSION_KEY = "com.nanzhipro.web-design-system-extractor";
const toRgb = converter("rgb");

function clamp(value, minimum, maximum) {
    return Math.min(maximum, Math.max(minimum, value));
}

function byteToHex(value) {
    return Math.round(clamp(value, 0, 255)).toString(16).padStart(2, "0");
}

export function parseCssColor(input) {
    if (typeof input !== "string") {
        return null;
    }
    let converted;
    try {
        converted = toRgb(input.trim());
    } catch {
        return null;
    }
    if (!converted || ![converted.r, converted.g, converted.b].every(Number.isFinite)) {
        return null;
    }
    const r = clamp(converted.r, 0, 1) * 255;
    const g = clamp(converted.g, 0, 1) * 255;
    const b = clamp(converted.b, 0, 1) * 255;
    const a = clamp(converted.alpha ?? 1, 0, 1);
    return {
        r,
        g,
        b,
        a,
        hex: `#${byteToHex(r)}${byteToHex(g)}${byteToHex(b)}${a < 1 ? byteToHex(a * 255) : ""}`,
    };
}

export function parsePixelDimension(value) {
    if (typeof value !== "string") {
        return null;
    }
    const match = /^(-?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?)px$/i.exec(value.trim());
    if (!match) {
        return null;
    }
    const number = Number(match[1]);
    return Number.isFinite(number) ? Object.is(number, -0) ? 0 : number : null;
}

export function parseCssDimension(value) {
    if (typeof value !== "string") {
        return null;
    }
    const match = /^(-?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?)(px|rem)$/i.exec(value.trim());
    if (!match) {
        return null;
    }
    const number = Number(match[1]);
    return Number.isFinite(number) ? { value: Object.is(number, -0) ? 0 : number, unit: match[2] } : null;
}

function dimensionName(value) {
    const sign = value < 0 ? "negative-" : "";
    const normalized = Math.abs(value).toString().replace(".", "-");
    return `${sign}px-${normalized}`;
}

function colorName(color) {
    return `${color.a < 1 ? "rgba" : "hex"}-${color.hex.slice(1)}`;
}

function dtcgColorValue(color) {
    return {
        colorSpace: "srgb",
        components: [color.r / 255, color.g / 255, color.b / 255].map((number) => Number(number.toFixed(6))),
        alpha: Number(color.a.toFixed(6)),
    };
}

function tokenSource(count, roles, samples) {
    return {
        [EXTENSION_KEY]: {
            observedCount: count,
            observedRoles: roles,
            sampleElements: samples,
            confidence: "observed",
        },
    };
}

function mergeRecords(captures, property) {
    const merged = new Map();
    for (const capture of captures) {
        for (const item of capture.evidence[property] ?? []) {
            let target = merged.get(item.value);
            if (!target) {
                target = { value: item.value, count: 0, roles: {}, samples: [], viewports: new Set() };
                merged.set(item.value, target);
            }
            target.count += item.count;
            target.viewports.add(capture.viewport.name);
            for (const [role, count] of Object.entries(item.roles ?? {})) {
                target.roles[role] = (target.roles[role] ?? 0) + count;
            }
            for (const sample of item.samples ?? []) {
                const qualified = `${capture.viewport.name}:${sample}`;
                if (target.samples.length < 8 && !target.samples.includes(qualified)) {
                    target.samples.push(qualified);
                }
            }
        }
    }
    return Array.from(merged.values())
        .map((item) => ({ ...item, viewports: Array.from(item.viewports).sort() }))
        .sort((left, right) => right.count - left.count || left.value.localeCompare(right.value));
}

function splitFontFamily(value) {
    const families = [];
    const expression = /"([^"]+)"|'([^']+)'|([^,]+)/g;
    let match;
    while ((match = expression.exec(value)) !== null) {
        const family = (match[1] || match[2] || match[3] || "").trim();
        if (family) {
            families.push(family);
        }
    }
    return families;
}

function uniqueBy(items, keyFunction) {
    const seen = new Set();
    return items.filter((item) => {
        const key = keyFunction(item);
        if (seen.has(key)) {
            return false;
        }
        seen.add(key);
        return true;
    });
}

function makeDimensionGroup(records, role, { allowNegative = false, limit = 32 } = {}) {
    const candidates = records
        .filter((item) => (item.roles[role] ?? 0) > 0)
        .map((item) => ({ ...item, number: parsePixelDimension(item.value) }))
        .filter((item) => item.number !== null && (allowNegative || item.number >= 0));
    const unique = uniqueBy(candidates, (item) => item.number).slice(0, limit);
    const group = { $type: "dimension" };
    for (const item of unique.sort((left, right) => left.number - right.number)) {
        group[dimensionName(item.number)] = {
            $value: { value: item.number, unit: "px" },
            $description: `Observed ${role} value on ${item.count} rendered declarations.`,
            $extensions: tokenSource(item.count, item.roles, item.samples),
        };
    }
    return group;
}

function relativeLuminance(color) {
    const channels = [color.r, color.g, color.b].map((channel) => {
        const normalized = channel / 255;
        return normalized <= 0.04045 ? normalized / 12.92 : ((normalized + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
}

export function contrastRatio(foreground, background) {
    const front = relativeLuminance(foreground);
    const back = relativeLuminance(background);
    return (Math.max(front, back) + 0.05) / (Math.min(front, back) + 0.05);
}

function buildContrastAudit(records) {
    const audited = [];
    let unauditableCount = 0;
    let likelyDecorativeOccurrences = 0;
    for (const item of records) {
        let pair;
        try {
            pair = JSON.parse(item.value);
        } catch {
            unauditableCount += item.count;
            continue;
        }
        if (pair.likelyDecorative) {
            likelyDecorativeOccurrences += item.count;
            continue;
        }
        const foreground = parseCssColor(pair.foreground);
        const background = parseCssColor(pair.background);
        if (!foreground || !background || foreground.a < 1 || background.a < 1 || pair.backgroundImage) {
            unauditableCount += item.count;
            continue;
        }
        const fontSize = parsePixelDimension(pair.fontSize) ?? 0;
        const fontWeight = Number.parseInt(pair.fontWeight, 10) || 400;
        const largeText = fontSize >= 24 || (fontSize >= 18.66 && fontWeight >= 700);
        const ratio = contrastRatio(foreground, background);
        audited.push({
            foreground: foreground.hex,
            background: background.hex,
            ratio: Number(ratio.toFixed(2)),
            threshold: largeText ? 3 : 4.5,
            pass: ratio >= (largeText ? 3 : 4.5),
            observedCount: item.count,
            samples: item.samples,
        });
    }
    audited.sort((left, right) => right.observedCount - left.observedCount || left.ratio - right.ratio);
    return {
        method: "WCAG 2.x contrast ratio on opaque computed foreground/background pairs",
        audited,
        passingOccurrences: audited.filter((item) => item.pass).reduce((sum, item) => sum + item.observedCount, 0),
        failingOccurrences: audited.filter((item) => !item.pass).reduce((sum, item) => sum + item.observedCount, 0),
        unauditableOccurrences: unauditableCount,
        likelyDecorativeOccurrences,
    };
}

function buildComponentPatterns(captures) {
    const patterns = new Map();
    for (const capture of captures) {
        for (const component of capture.evidence.componentCandidates ?? []) {
            const key = JSON.stringify({
                tag: component.tag,
                role: component.role,
                backgroundColor: component.style.backgroundColor,
                border: component.style.border,
                borderRadius: component.style.borderRadius,
                boxShadow: component.style.boxShadow,
                fontSize: component.style.fontSize,
                fontWeight: component.style.fontWeight,
                padding: component.style.padding,
            });
            let pattern = patterns.get(key);
            if (!pattern) {
                pattern = {
                    tag: component.tag,
                    role: component.role,
                    style: component.style,
                    count: 0,
                    samples: [],
                    viewports: new Set(),
                };
                patterns.set(key, pattern);
            }
            pattern.count += 1;
            pattern.viewports.add(capture.viewport.name);
            if (pattern.samples.length < 5 && component.text && !pattern.samples.includes(component.text)) {
                pattern.samples.push(component.text);
            }
        }
    }
    return Array.from(patterns.values())
        .map((pattern) => ({ ...pattern, viewports: Array.from(pattern.viewports).sort() }))
        .sort((left, right) => right.count - left.count)
        .slice(0, 80);
}

function findDominantByRole(colors, role, { opaque = false } = {}) {
    return colors
        .filter((item) => item.parsed.a > 0 && (!opaque || item.parsed.a === 1) && (item.roles[role] ?? 0) > 0)
        .sort((left, right) => (right.roles[role] ?? 0) - (left.roles[role] ?? 0))[0];
}

function findByCssColor(colors, value) {
    const parsed = parseCssColor(value);
    return parsed ? colors.find((item) => item.parsed.hex === parsed.hex) : undefined;
}

function aliasToken(color, role) {
    if (!color) {
        return undefined;
    }
    return {
        $value: `{color.palette.${colorName(color.parsed)}}`,
        $description: `Provisional ${role} alias inferred from rendered usage frequency.`,
        $extensions: {
            [EXTENSION_KEY]: {
                confidence: "inferred",
                inference: "dominant-role-frequency",
                observedRole: role,
            },
        },
    };
}

function sourceVariableName(name) {
    return name
        .replace(/^--/, "")
        .replace(/[^a-zA-Z0-9_-]+/g, "-")
        .replace(/^-+|-+$/g, "")
        .slice(0, 96);
}

function buildSourceVariableTokens(captures) {
    const variables = new Map();
    const computed = captures[0]?.evidence.computedVariables ?? {};
    for (const element of ["html", "body", "main"]) {
        for (const item of computed[element] ?? []) {
            variables.set(item.name, item.value);
        }
    }

    function resolveValue(name, seen = new Set()) {
        if (seen.has(name)) {
            return null;
        }
        seen.add(name);
        const raw = variables.get(name);
        if (!raw) {
            return null;
        }
        const reference = /^var\(\s*(--[a-zA-Z0-9_-]+)(?:\s*,\s*([^)]*))?\s*\)$/.exec(raw);
        if (!reference) {
            return raw;
        }
        return resolveValue(reference[1], seen) ?? reference[2]?.trim() ?? null;
    }

    const color = { $type: "color" };
    const dimension = { $type: "dimension" };
    const usedNames = new Set();
    const relevantName = /(?:background|foreground|canvas|surface|content|text|border|primary|secondary|accent|muted|gray|grey|black|white|red|orange|yellow|green|blue|cyan|teal|purple|violet|pink|radius|space|spacing|gap|height|width|size)/i;
    const internalName = /^--(?:tw|radix|framer|motion)-/i;

    for (const name of Array.from(variables.keys()).sort()) {
        if (internalName.test(name) || !relevantName.test(name)) {
            continue;
        }
        const resolved = resolveValue(name);
        if (!resolved) {
            continue;
        }
        let tokenName = sourceVariableName(name);
        if (!tokenName) {
            continue;
        }
        while (usedNames.has(tokenName)) {
            tokenName = `${tokenName}-source`;
        }
        const parsedColor = parseCssColor(resolved);
        const parsedDimension = parseCssDimension(resolved);
        if (parsedColor) {
            usedNames.add(tokenName);
            color[tokenName] = {
                $value: dtcgColorValue(parsedColor),
                $description: `Active computed custom property ${name}; source value ${resolved}.`,
                $extensions: {
                    [EXTENSION_KEY]: {
                        confidence: "observed-source-variable",
                        originalName: name,
                        originalValue: resolved,
                    },
                },
            };
        } else if (parsedDimension !== null) {
            usedNames.add(tokenName);
            dimension[tokenName] = {
                $value: parsedDimension,
                $description: `Active computed custom property ${name}; source value ${resolved}.`,
                $extensions: {
                    [EXTENSION_KEY]: {
                        confidence: "observed-source-variable",
                        originalName: name,
                        originalValue: resolved,
                    },
                },
            };
        }
    }
    return { color, dimension };
}

function aliasSourceToken(sourceTokens, name, role) {
    if (!sourceTokens.color[name]) {
        return undefined;
    }
    return {
        $value: `{source.color.${name}}`,
        $description: `Semantic ${role} alias recovered from the active --${name} custom property.`,
        $extensions: {
            [EXTENSION_KEY]: {
                confidence: "observed-source-variable",
                originalName: `--${name}`,
            },
        },
    };
}

export function buildArtifacts(captures, metadata) {
    const colorRecords = mergeRecords(captures, "colors");
    const lengthRecords = mergeRecords(captures, "lengths");
    const typographyRecords = mergeRecords(captures, "typography");
    const contrastRecords = mergeRecords(captures, "contrastPairs");
    const parsedColors = colorRecords
        .map((item) => ({ ...item, parsed: parseCssColor(item.value) }))
        .filter((item) => item.parsed && item.parsed.a > 0);
    const rootColorRecords = captures.flatMap((capture) => {
        const rootStyles = capture.evidence.metadata.rootStyles ?? {};
        return Object.entries(rootStyles).flatMap(([element, style]) => {
            if (!style) {
                return [];
            }
            return [
                { value: style.color, role: "root-text", element },
                { value: style.backgroundColor, role: "root-background", element },
            ];
        });
    });
    for (const root of rootColorRecords) {
        const parsed = parseCssColor(root.value);
        if (!parsed || parsed.a === 0 || parsedColors.some((item) => item.parsed.hex === parsed.hex)) {
            continue;
        }
        parsedColors.push({
            value: root.value,
            count: 1,
            roles: { [root.role]: 1 },
            samples: [`root:${root.element}`],
            viewports: captures.map((capture) => capture.viewport.name),
            parsed,
        });
    }
    const allUniqueColors = uniqueBy(parsedColors, (item) => item.parsed.hex);
    const reusableColors = allUniqueColors.filter((item) =>
        Object.keys(item.roles ?? {}).some((role) => role !== "decorative-text"),
    );
    const selectedColorHexes = new Set(reusableColors.slice(0, 64).map((item) => item.parsed.hex));
    for (const root of rootColorRecords) {
        const parsed = parseCssColor(root.value);
        if (parsed?.a > 0) {
            selectedColorHexes.add(parsed.hex);
        }
    }
    const uniqueColors = allUniqueColors.filter((item) => selectedColorHexes.has(item.parsed.hex));

    const palette = {};
    for (const item of uniqueColors) {
        palette[colorName(item.parsed)] = {
            $value: dtcgColorValue(item.parsed),
            $description: `Observed computed color ${item.value} on ${item.count} declarations.`,
            $extensions: tokenSource(item.count, item.roles, item.samples),
        };
    }

    const fontFamilies = new Map();
    const fontWeights = new Map();
    for (const item of typographyRecords) {
        try {
            const typography = JSON.parse(item.value);
            const families = splitFontFamily(typography.fontFamily);
            const familyKey = families.join("|");
            if (families.length > 0 && !fontFamilies.has(familyKey)) {
                fontFamilies.set(familyKey, { families, ...item });
            }
            const weight = Number.parseInt(typography.fontWeight, 10);
            if (Number.isInteger(weight) && weight >= 1 && weight <= 1000 && !fontWeights.has(weight)) {
                fontWeights.set(weight, { weight, ...item });
            }
        } catch {
            // Raw typography remains available even when an individual tuple is malformed.
        }
    }

    const fontFamilyGroup = { $type: "fontFamily" };
    Array.from(fontFamilies.values())
        .slice(0, 16)
        .forEach((item, index) => {
            fontFamilyGroup[`stack-${String(index + 1).padStart(2, "0")}`] = {
                $value: item.families,
                $description: `Observed font stack on ${item.count} rendered elements.`,
                $extensions: tokenSource(item.count, item.roles, item.samples),
            };
        });

    const fontWeightGroup = { $type: "fontWeight" };
    Array.from(fontWeights.values())
        .sort((left, right) => left.weight - right.weight)
        .forEach((item) => {
            fontWeightGroup[`weight-${item.weight}`] = {
                $value: item.weight,
                $description: `Observed font weight on ${item.count} rendered elements.`,
                $extensions: tokenSource(item.count, item.roles, item.samples),
            };
        });

    const primaryRootStyles = captures[0]?.evidence.metadata.rootStyles ?? {};
    const rootContent =
        findByCssColor(uniqueColors, primaryRootStyles.body?.color) ??
        findByCssColor(uniqueColors, primaryRootStyles.main?.color) ??
        findDominantByRole(uniqueColors, "text", { opaque: true });
    const rootCanvas =
        findByCssColor(uniqueColors, primaryRootStyles.body?.backgroundColor) ??
        findByCssColor(uniqueColors, primaryRootStyles.main?.backgroundColor) ??
        findDominantByRole(uniqueColors, "background", { opaque: true });
    const sourceTokens = buildSourceVariableTokens(captures);
    const semantic = {
        $type: "color",
        canvas: aliasSourceToken(sourceTokens, "background", "canvas") ?? aliasToken(rootCanvas, "root background"),
        content: aliasSourceToken(sourceTokens, "foreground", "content") ?? aliasToken(rootContent, "root text"),
        border:
            aliasSourceToken(sourceTokens, "border", "border") ??
            aliasToken(findDominantByRole(uniqueColors, "border"), "border"),
    };
    for (const [key, value] of Object.entries(semantic)) {
        if (value === undefined) {
            delete semantic[key];
        }
    }

    const tokens = {
        $schema: "https://www.designtokens.org/schemas/2025.10/format.json",
        color: {
            $type: "color",
            palette,
            semantic,
        },
        dimension: {
            spacing: makeDimensionGroup(lengthRecords, "spacing", { allowNegative: true }),
            radius: makeDimensionGroup(lengthRecords, "radius"),
            borderWidth: makeDimensionGroup(lengthRecords, "border-width"),
            fontSize: makeDimensionGroup(lengthRecords, "font-size"),
            lineHeight: makeDimensionGroup(lengthRecords, "line-height"),
            letterSpacing: makeDimensionGroup(lengthRecords, "letter-spacing", { allowNegative: true }),
        },
        typography: {
            fontFamily: fontFamilyGroup,
            fontWeight: fontWeightGroup,
        },
        source: sourceTokens,
    };

    const raw = {
        metadata,
        viewports: captures.map((capture) => ({
            ...capture.viewport,
            finalUrl: capture.evidence.metadata.url,
            document: capture.evidence.metadata.document,
            rootStyles: capture.evidence.metadata.rootStyles,
            coverage: capture.evidence.coverage,
        })),
        observed: {
            colors: colorRecords,
            lengths: lengthRecords,
            typography: typographyRecords,
            shadows: mergeRecords(captures, "shadows"),
            gradients: mergeRecords(captures, "gradients"),
            motion: mergeRecords(captures, "motion"),
            contrastPairs: contrastRecords,
        },
        cssVariables: captures.map((capture) => ({
            viewport: capture.viewport.name,
            values: capture.evidence.cssVariables,
        })),
        computedVariables: captures.map((capture) => ({
            viewport: capture.viewport.name,
            values: capture.evidence.computedVariables,
        })),
        mediaQueries: Array.from(new Set(captures.flatMap((capture) => capture.evidence.mediaQueries))).sort(),
        fonts: uniqueBy(captures.flatMap((capture) => capture.evidence.fonts), (font) => JSON.stringify(font)),
        assets: uniqueBy(captures.flatMap((capture) => capture.evidence.assets), (asset) => `${asset.type}|${asset.url}`),
        componentPatterns: buildComponentPatterns(captures),
        accessibility: buildContrastAudit(contrastRecords),
    };

    return { tokens, raw, css: renderCssVariables(tokens) };
}

function cssValue(value, path) {
    if (typeof value === "string" && value.startsWith("{") && value.endsWith("}")) {
        const target = value.slice(1, -1).split(".").map((part) => part.replace(/([a-z0-9])([A-Z])/g, "$1-$2").toLowerCase());
        return `var(--${target.join("-")})`;
    }
    if (path[0] === "color" && value && typeof value === "object") {
        const [r, g, b] = value.components.map((component) => Math.round(component * 255));
        if (value.alpha < 1) {
            return `rgba(${r}, ${g}, ${b}, ${Number(value.alpha.toFixed(4))})`;
        }
        return `#${byteToHex(r)}${byteToHex(g)}${byteToHex(b)}`;
    }
    if (value && typeof value === "object" && typeof value.value === "number" && value.unit) {
        return `${value.value}${value.unit}`;
    }
    if (Array.isArray(value)) {
        return value.map((part) => (part.includes(" ") ? `"${part}"` : part)).join(", ");
    }
    return String(value);
}

export function renderCssVariables(tokens) {
    const declarations = [];
    function visit(node, path = [], inheritedType) {
        if (!node || typeof node !== "object") {
            return;
        }
        const type = node.$type ?? inheritedType;
        if (Object.hasOwn(node, "$value")) {
            const cssName = path.map((part) => part.replace(/([a-z0-9])([A-Z])/g, "$1-$2").toLowerCase()).join("-");
            declarations.push(`  --${cssName}: ${cssValue(node.$value, path, type)};`);
            return;
        }
        for (const [key, value] of Object.entries(node)) {
            if (!key.startsWith("$")) {
                visit(value, [...path, key], type);
            }
        }
    }
    visit(tokens);
    return [":root {", ...declarations, "}", ""].join("\n");
}
