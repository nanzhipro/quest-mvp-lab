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

export function buildGoogleDesignSystem(raw, title) {
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

    const colors = {};
    for (const [name, value] of [
        ["primary", primary],
        ["on-primary", onPrimary],
        ["surface", surface],
        ["on-surface", onSurface],
    ]) {
        if (value) {
            colors[name] = value;
        }
    }

    const typography = buildTypography(raw);
    const rounded = buildRounded(raw, primaryComponent);
    const spacing = buildSpacing(raw, primaryComponent);
    const components = {};
    if (surface && onSurface) {
        components["surface-default"] = {
            backgroundColor: "{colors.surface}",
            textColor: "{colors.on-surface}",
            ...(typography["body-md"] ? { typography: "{typography.body-md}" } : {}),
        };
    }
    if (primary && onPrimary && primaryComponent) {
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
