import { stringify } from "yaml";

import { contrastRatio, parseCssColor, parsePixelDimension } from "./normalize.mjs";

export const GOOGLE_DESIGN_SECTION_ORDER = [
    "Overview",
    "Colors",
    "Typography",
    "Layout",
    "Elevation & Depth",
    "Shapes",
    "Components",
    "Do's and Don'ts",
];

function parseTypography(item) {
    try {
        return JSON.parse(item.value);
    } catch {
        return null;
    }
}

function isCssDimension(value) {
    return typeof value === "string" && /^-?(?:\d+\.?\d*|\.\d+)(?:px|em|rem)$/.test(value.trim());
}

function dimensionString(value) {
    return `${Number(value.toFixed(4))}px`;
}

function dimensionKey(value) {
    return `px-${Number(value.toFixed(4)).toString().replace(".", "-")}`;
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

function renderFontFamily(value) {
    const parts = Array.isArray(value) ? value : [value];
    return parts
        .map((part) => String(part).trim().replace(/^(["'])(.*)\1$/, "$2").trim())
        .map((part) => (/\s/.test(part) ? `"${part.replace(/"/g, '\\"')}"` : part))
        .join(", ");
}

function primitiveTokenValues(group, inheritedType, result = []) {
    if (!group || typeof group !== "object" || Array.isArray(group)) {
        return result;
    }
    const type = group.$type ?? inheritedType;
    if (Object.hasOwn(group, "$value")) {
        if (typeof group.$value !== "string" || !/^\{[^{}]+\}$/.test(group.$value)) {
            result.push({ type, value: group.$value });
        }
        return result;
    }
    for (const [name, child] of Object.entries(group)) {
        if (!name.startsWith("$")) {
            primitiveTokenValues(child, type, result);
        }
    }
    return result;
}

function canonicalTokenValue(group, type, observedValue) {
    for (const token of primitiveTokenValues(group, type)) {
        if (token.type !== type) {
            continue;
        }
        if (type === "color") {
            const observed = parseCssColor(observedValue);
            const canonical = dtcgColorHex(token.value);
            if (observed && canonical && observed.hex === canonical) {
                return canonical;
            }
        } else if (type === "dimension") {
            const observed = isCssDimension(observedValue) ? observedValue.trim() : null;
            const canonical =
                token.value && typeof token.value.value === "number" && token.value.unit
                    ? `${token.value.value}${token.value.unit}`
                    : null;
            if (observed && canonical && observed === canonical) {
                return canonical;
            }
        } else if (type === "fontFamily" && normalizeFontFamily(observedValue) === normalizeFontFamily(token.value)) {
            return renderFontFamily(token.value);
        } else if (type === "fontWeight" && Number(observedValue) === Number(token.value)) {
            return token.value;
        }
    }
    return null;
}

function canonicalizeTypography(typography, tokens) {
    const result = {};
    for (const [name, observed] of Object.entries(typography)) {
        const fontFamily = canonicalTokenValue(tokens?.typography?.fontFamily, "fontFamily", observed.fontFamily);
        const fontSize = canonicalTokenValue(tokens?.dimension?.fontSize, "dimension", observed.fontSize);
        if (!fontFamily || !fontSize) {
            continue;
        }
        const canonical = { fontFamily, fontSize };
        if (observed.fontWeight !== undefined) {
            const fontWeight = canonicalTokenValue(tokens?.typography?.fontWeight, "fontWeight", observed.fontWeight);
            if (fontWeight !== null) {
                canonical.fontWeight = fontWeight;
            }
        }
        for (const [property, group] of [
            ["lineHeight", tokens?.dimension?.lineHeight],
            ["letterSpacing", tokens?.dimension?.letterSpacing],
        ]) {
            if (observed[property] !== undefined) {
                const value = canonicalTokenValue(group, "dimension", observed[property]);
                if (value !== null) {
                    canonical[property] = value;
                }
            }
        }
        result[name] = canonical;
    }
    return result;
}

function canonicalizeScale(scale, group) {
    return Object.fromEntries(
        Object.entries(scale).flatMap(([name, value]) => {
            const canonical = canonicalTokenValue(group, "dimension", value);
            return canonical === null ? [] : [[name, canonical]];
        }),
    );
}

export function collectActiveVariables(raw) {
    const variables = new Map();
    const values = raw.computedVariables?.[0]?.values ?? {};
    for (const element of ["html", "body", "main"]) {
        for (const item of values[element] ?? []) {
            variables.set(item.name, item.value);
        }
    }
    return Array.from(variables, ([name, value]) => ({ name, value })).sort((left, right) =>
        left.name.localeCompare(right.name),
    );
}

function findVariableColor(variableMap, names) {
    for (const name of names) {
        const value = variableMap.get(name);
        const parsed = parseCssColor(value);
        if (parsed?.a > 0) {
            return parsed.hex;
        }
    }
    return null;
}

function findRootColor(raw, property) {
    const rootStyles = raw.viewports?.[0]?.rootStyles ?? {};
    for (const element of ["body", "main", "html"]) {
        const parsed = parseCssColor(rootStyles[element]?.[property]);
        if (parsed?.a > 0) {
            return parsed.hex;
        }
    }
    return null;
}

function findDominantColor(raw, role, { opaque = false } = {}) {
    return raw.observed.colors
        .map((item) => ({ ...item, parsed: parseCssColor(item.value) }))
        .filter((item) => item.parsed?.a > 0 && (!opaque || item.parsed.a === 1) && (item.roles?.[role] ?? 0) > 0)
        .sort(
            (left, right) =>
                (right.roles?.[role] ?? 0) - (left.roles?.[role] ?? 0) || right.count - left.count,
        )[0]?.parsed.hex;
}

function paddingDimensions(value) {
    if (typeof value !== "string") {
        return [];
    }
    return value
        .trim()
        .split(/\s+/)
        .map(parsePixelDimension)
        .filter((part) => part !== null && part >= 0);
}

function selectPrimaryComponent(raw, preferredPrimary) {
    const candidates = raw.componentPatterns
        .filter((component) => ["a", "button", "input"].includes(component.tag) || component.role === "button")
        .map((component) => {
            const background = parseCssColor(component.style.backgroundColor);
            const foreground = parseCssColor(component.style.color);
            const padding = paddingDimensions(component.style.padding);
            if (!background || !foreground || background.a !== 1 || foreground.a !== 1 || background.hex === foreground.hex) {
                return null;
            }
            if (!padding.some((value) => value > 0) && component.tag !== "button") {
                return null;
            }
            const contrast = contrastRatio(foreground, background);
            const score =
                (background.hex === preferredPrimary ? 1000 : 0) +
                (padding.some((value) => value > 0) ? 100 : 0) +
                (component.tag === "button" ? 20 : 0) +
                component.count * 10;
            return { component, background: background.hex, foreground: foreground.hex, padding, contrast, score };
        })
        .filter(Boolean)
        .filter((candidate) => candidate.contrast >= 3)
        .sort((left, right) => right.score - left.score);
    return candidates[0] ?? null;
}

function buildTypography(raw) {
    const candidates = raw.observed.typography
        .map((item) => ({ ...item, parsed: parseTypography(item) }))
        .filter((item) => item.parsed?.fontFamily && isCssDimension(item.parsed.fontSize));
    const selected = [];

    function add(name, candidate) {
        if (!candidate || selected.some((item) => item.candidate.value === candidate.value)) {
            return;
        }
        selected.push({ name, candidate });
    }

    const byRoles = (...roles) => candidates.find((item) => roles.some((role) => (item.roles?.[role] ?? 0) > 0));
    add("display-lg", byRoles("h1"));
    add("headline-lg", byRoles("h2"));
    add("headline-md", byRoles("h3", "h4"));
    add("body-md", byRoles("p", "li", "blockquote", "div") ?? candidates[0]);
    add("label-lg", byRoles("button", "input", "select", "textarea"));
    add("label-md", byRoles("a"));

    for (const candidate of candidates) {
        if (selected.length >= 12) {
            break;
        }
        add(`observed-${String(selected.length + 1).padStart(2, "0")}`, candidate);
    }

    const typography = {};
    for (const { name, candidate } of selected) {
        const source = candidate.parsed;
        const token = {
            fontFamily: source.fontFamily,
            fontSize: source.fontSize,
        };
        const weight = Number.parseInt(source.fontWeight, 10);
        if (Number.isInteger(weight) && weight >= 1 && weight <= 1000) {
            token.fontWeight = weight;
        }
        if (isCssDimension(source.lineHeight) || /^\d*\.?\d+$/.test(source.lineHeight ?? "")) {
            token.lineHeight = /^\d*\.?\d+$/.test(source.lineHeight) ? Number(source.lineHeight) : source.lineHeight;
        }
        if (isCssDimension(source.letterSpacing)) {
            token.letterSpacing = source.letterSpacing;
        }
        typography[name] = token;
    }
    return typography;
}

function selectDimensions(raw, role, { includeZero = false, preferred = [] } = {}) {
    const byValue = new Map();
    for (const item of raw.observed.lengths) {
        if ((item.roles?.[role] ?? 0) <= 0) {
            continue;
        }
        const value = parsePixelDimension(item.value);
        if (value === null || value < 0 || (!includeZero && value === 0)) {
            continue;
        }
        const existing = byValue.get(value);
        if (!existing || item.count > existing.count) {
            byValue.set(value, { value, count: item.count });
        }
    }
    for (const value of preferred) {
        if (value >= 0 && (includeZero || value > 0) && !byValue.has(value)) {
            byValue.set(value, { value, count: 0 });
        }
    }
    return Array.from(byValue.values());
}

function buildSpacing(raw, primaryComponent) {
    const candidates = selectDimensions(raw, "spacing", { preferred: primaryComponent?.padding ?? [] })
        .filter((item) => item.value <= 256)
        .sort((left, right) => {
            const leftGrid = Number.isInteger(left.value / 4) ? 1 : 0;
            const rightGrid = Number.isInteger(right.value / 4) ? 1 : 0;
            return rightGrid - leftGrid || right.count - left.count || left.value - right.value;
        })
        .slice(0, 8)
        .sort((left, right) => left.value - right.value);
    return Object.fromEntries(candidates.map((item) => [dimensionKey(item.value), dimensionString(item.value)]));
}

function buildRounded(raw, primaryComponent) {
    const preferredRadius = parsePixelDimension(primaryComponent?.component.style.borderRadius);
    const all = selectDimensions(raw, "radius", {
        includeZero: true,
        preferred: preferredRadius === null ? [] : [preferredRadius],
    });
    const ordinary = all
        .filter((item) => item.value <= 256)
        .sort((left, right) => right.count - left.count || left.value - right.value)
        .slice(0, 6);
    const full = all.filter((item) => item.value > 256).sort((left, right) => right.count - left.count)[0];
    const selected = [...ordinary, ...(full ? [full] : [])].sort((left, right) => left.value - right.value);
    const rounded = {};
    for (const item of selected) {
        const name = item.value === 0 ? "none" : item.value > 256 ? "full" : dimensionKey(item.value);
        rounded[name] = dimensionString(item.value);
    }
    return rounded;
}

function roundedReference(rounded, rawValue) {
    const value = parsePixelDimension(rawValue);
    if (value === null) {
        return null;
    }
    const name = value === 0 ? "none" : value > 256 ? "full" : dimensionKey(value);
    return Object.hasOwn(rounded, name) ? `{rounded.${name}}` : null;
}

export function buildGoogleDesignSystem(raw, title, tokens) {
    const activeVariables = collectActiveVariables(raw);
    const variableMap = new Map(activeVariables.map((item) => [item.name, item.value]));
    const preferredPrimary = findVariableColor(variableMap, [
        "--primary",
        "--color-primary",
        "--brand",
        "--color-brand",
        "--accent",
        "--color-accent",
    ]);
    const primaryComponent = selectPrimaryComponent(raw, preferredPrimary);
    const primary =
        primaryComponent?.background ?? preferredPrimary ?? findDominantColor(raw, "background", { opaque: true });
    const onPrimary =
        primaryComponent?.foreground ??
        findVariableColor(variableMap, ["--primary-foreground", "--on-primary", "--color-on-primary"]) ??
        findDominantColor(raw, "text", { opaque: true });
    const surface =
        findVariableColor(variableMap, ["--background", "--canvas", "--color-canvas", "--surface", "--color-surface"]) ??
        findRootColor(raw, "backgroundColor") ??
        findDominantColor(raw, "background", { opaque: true });
    const onSurface =
        findVariableColor(variableMap, ["--foreground", "--content", "--color-ink", "--text", "--color-text"]) ??
        findRootColor(raw, "color") ??
        findDominantColor(raw, "text", { opaque: true });

    const observedColors = {};
    for (const [name, value] of [
        ["primary", primary],
        ["on-primary", onPrimary],
        ["surface", surface],
        ["on-surface", onSurface],
    ]) {
        if (value) {
            observedColors[name] = value;
        }
    }

    const colorTokens = { color: tokens?.color, source: { color: tokens?.source?.color } };
    const colors = tokens
        ? Object.fromEntries(
              Object.entries(observedColors).flatMap(([name, value]) => {
                  const canonical = canonicalTokenValue(colorTokens, "color", value);
                  return canonical === null ? [] : [[name, canonical]];
              }),
          )
        : observedColors;
    const observedTypography = buildTypography(raw);
    const observedRounded = buildRounded(raw, primaryComponent);
    const observedSpacing = buildSpacing(raw, primaryComponent);
    const typography = tokens ? canonicalizeTypography(observedTypography, tokens) : observedTypography;
    const rounded = tokens ? canonicalizeScale(observedRounded, tokens.dimension?.radius) : observedRounded;
    const spacing = tokens ? canonicalizeScale(observedSpacing, tokens.dimension?.spacing) : observedSpacing;
    const components = {};
    if (colors.surface && colors["on-surface"]) {
        components["surface-default"] = {
            backgroundColor: "{colors.surface}",
            textColor: "{colors.on-surface}",
            ...(typography["body-md"] ? { typography: "{typography.body-md}" } : {}),
        };
    }
    if (colors.primary && colors["on-primary"] && primaryComponent) {
        components["button-primary"] = {
            backgroundColor: "{colors.primary}",
            textColor: "{colors.on-primary}",
            ...(typography["label-lg"] ? { typography: "{typography.label-lg}" } : {}),
            ...(roundedReference(rounded, primaryComponent.component.style.borderRadius)
                ? { rounded: roundedReference(rounded, primaryComponent.component.style.borderRadius) }
                : {}),
        };
    }

    const omitted = [];
    for (const [section, values] of [
        ["colors", colors],
        ["typography", typography],
        ["spacing", spacing],
        ["rounded", rounded],
        ["components", components],
    ]) {
        if (Object.keys(values).length === 0) {
            omitted.push({ section, reason: `No ${section} values were safely observable in the declared capture profile.` });
        }
    }

    return {
        version: "alpha",
        name: `${title} - Extracted Design System`,
        description: "Evidence-first reconstruction from rendered web-page observations; semantics remain capture-scoped.",
        ...(omitted.length > 0 ? { omitted } : {}),
        ...(Object.keys(colors).length > 0 ? { colors } : {}),
        ...(Object.keys(typography).length > 0 ? { typography } : {}),
        ...(Object.keys(rounded).length > 0 ? { rounded } : {}),
        ...(Object.keys(spacing).length > 0 ? { spacing } : {}),
        ...(Object.keys(components).length > 0 ? { components } : {}),
    };
}

export function renderGoogleFrontmatter(designSystem) {
    const yaml = stringify(designSystem, { lineWidth: 0 }).trimEnd();
    return `---\n${yaml}\n---`;
}
