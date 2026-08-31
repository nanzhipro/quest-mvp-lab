// This function is serialized by Playwright and runs inside the inspected page.
// Keep every helper inside the function body.
export function collectPageEvidence({ maxElements = 20_000, maxSamplesPerValue = 5 } = {}) {
    const maps = {
        colors: new Map(),
        lengths: new Map(),
        typography: new Map(),
        shadows: new Map(),
        gradients: new Map(),
        motion: new Map(),
        contrastPairs: new Map(),
        cssVariables: new Map(),
    };
    const assets = new Map();
    const mediaQueries = new Set();
    const componentCandidates = [];
    const nodes = [];
    const stack = [document.documentElement];
    let openShadowRoots = 0;

    while (stack.length > 0 && nodes.length < maxElements) {
        const node = stack.pop();
        if (!(node instanceof Element)) {
            continue;
        }
        nodes.push(node);
        const children = Array.from(node.children);
        for (let index = children.length - 1; index >= 0; index -= 1) {
            stack.push(children[index]);
        }
        if (node.shadowRoot) {
            openShadowRoots += 1;
            const shadowChildren = Array.from(node.shadowRoot.children);
            for (let index = shadowChildren.length - 1; index >= 0; index -= 1) {
                stack.push(shadowChildren[index]);
            }
        }
    }

    function sampleLabel(element) {
        const tag = element.tagName.toLowerCase();
        if (element.id) {
            return `${tag}#${element.id.slice(0, 80)}`;
        }
        const role = element.getAttribute("role");
        const classes = Array.from(element.classList).slice(0, 3).join(".");
        return [tag, role ? `[role=${role}]` : "", classes ? `.${classes}` : ""].join("").slice(0, 160);
    }

    function record(map, value, sample, role) {
        if (!value || value === "none" || value === "normal") {
            return;
        }
        let item = map.get(value);
        if (!item) {
            item = { value, count: 0, samples: [], roles: {} };
            map.set(value, item);
        }
        item.count += 1;
        if (sample && item.samples.length < maxSamplesPerValue && !item.samples.includes(sample)) {
            item.samples.push(sample);
        }
        if (role) {
            item.roles[role] = (item.roles[role] ?? 0) + 1;
        }
    }

    function recordAsset(url, type, sample) {
        if (!url || url.startsWith("data:")) {
            return;
        }
        let absolute;
        try {
            absolute = new URL(url, document.baseURI).href;
        } catch {
            return;
        }
        const key = `${type}|${absolute}`;
        let item = assets.get(key);
        if (!item) {
            item = { url: absolute, type, count: 0, samples: [] };
            assets.set(key, item);
        }
        item.count += 1;
        if (sample && item.samples.length < maxSamplesPerValue && !item.samples.includes(sample)) {
            item.samples.push(sample);
        }
    }

    function isTransparent(value) {
        return (
            !value ||
            value === "transparent" ||
            value === "rgba(0, 0, 0, 0)" ||
            value === "rgb(0 0 0 / 0)"
        );
    }

    function isVisible(element, style, rect) {
        return (
            style.display !== "none" &&
            style.visibility !== "hidden" &&
            style.visibility !== "collapse" &&
            Number(style.opacity) !== 0 &&
            rect.width > 0 &&
            rect.height > 0
        );
    }

    function effectiveBackground(element) {
        let current = element;
        while (current) {
            const style = getComputedStyle(current);
            if (!isTransparent(style.backgroundColor)) {
                return { color: style.backgroundColor, image: style.backgroundImage };
            }
            if (style.backgroundImage && style.backgroundImage !== "none") {
                return { color: style.backgroundColor, image: style.backgroundImage };
            }
            current = current.parentElement;
        }
        return { color: getComputedStyle(document.documentElement).backgroundColor, image: "none" };
    }

    function addBackgroundAssets(value, sample) {
        const expression = /url\((?:"|')?([^"')]+)(?:"|')?\)/g;
        let match;
        while ((match = expression.exec(value)) !== null) {
            recordAsset(match[1], "background-image", sample);
        }
    }

    function compactText(element) {
        return (element.innerText || element.getAttribute("aria-label") || "")
            .replace(/\s+/g, " ")
            .trim()
            .slice(0, 120);
    }

    const textLengthProperties = [
        ["fontSize", "font-size"],
        ["lineHeight", "line-height"],
        ["letterSpacing", "letter-spacing"],
    ];
    const boxLengthProperties = [
        ["paddingTop", "spacing"],
        ["paddingRight", "spacing"],
        ["paddingBottom", "spacing"],
        ["paddingLeft", "spacing"],
        ["marginTop", "spacing"],
        ["marginRight", "spacing"],
        ["marginBottom", "spacing"],
        ["marginLeft", "spacing"],
        ["rowGap", "spacing"],
        ["columnGap", "spacing"],
        ["borderTopLeftRadius", "radius"],
        ["borderTopRightRadius", "radius"],
        ["borderBottomRightRadius", "radius"],
        ["borderBottomLeftRadius", "radius"],
        ["borderTopWidth", "border-width"],
        ["borderRightWidth", "border-width"],
        ["borderBottomWidth", "border-width"],
        ["borderLeftWidth", "border-width"],
    ];

    function directTextContent(element) {
        if (element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement || element instanceof HTMLSelectElement) {
            return element.value || element.getAttribute("placeholder") || element.getAttribute("aria-label") || "";
        }
        return Array.from(element.childNodes)
            .filter((node) => node.nodeType === Node.TEXT_NODE)
            .map((node) => node.textContent || "")
            .join(" ")
            .replace(/\s+/g, " ")
            .trim();
    }

    let visibleElements = 0;
    let hiddenElements = 0;
    let textElements = 0;
    let pseudoElements = 0;
    let canvasElements = 0;
    let svgElements = 0;

    for (const element of nodes) {
        const style = getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        if (!isVisible(element, style, rect)) {
            hiddenElements += 1;
            continue;
        }
        visibleElements += 1;
        const sample = sampleLabel(element);
        const directText = directTextContent(element);
        const carriesText = Boolean(directText);
        const likelyDecorativeText =
            carriesText &&
            element.tagName.toLowerCase() === "span" &&
            Array.from(directText).length <= 1 &&
            !element.getAttribute("aria-label") &&
            !element.getAttribute("role");

        if (carriesText && !isTransparent(style.color)) {
            record(maps.colors, style.color, sample, likelyDecorativeText ? "decorative-text" : "text");
        }
        if (!isTransparent(style.backgroundColor)) {
            record(maps.colors, style.backgroundColor, sample, "background");
        }
        const borderSides = [
            ["borderTopColor", "borderTopWidth", "borderTopStyle"],
            ["borderRightColor", "borderRightWidth", "borderRightStyle"],
            ["borderBottomColor", "borderBottomWidth", "borderBottomStyle"],
            ["borderLeftColor", "borderLeftWidth", "borderLeftStyle"],
        ];
        for (const [colorProperty, widthProperty, styleProperty] of borderSides) {
            if (style[styleProperty] !== "none" && style[widthProperty] !== "0px" && !isTransparent(style[colorProperty])) {
                record(maps.colors, style[colorProperty], sample, "border");
            }
        }
        if (style.outlineStyle !== "none" && style.outlineWidth !== "0px" && !isTransparent(style.outlineColor)) {
            record(maps.colors, style.outlineColor, sample, "outline");
        }
        if (element instanceof SVGElement) {
            if (!isTransparent(style.fill) && style.fill !== "none") {
                record(maps.colors, style.fill, sample, "fill");
            }
            if (!isTransparent(style.stroke) && style.stroke !== "none") {
                record(maps.colors, style.stroke, sample, "stroke");
            }
        }
        for (const [property, role] of boxLengthProperties) {
            record(maps.lengths, style[property], sample, role);
        }
        if (carriesText && !likelyDecorativeText) {
            for (const [property, role] of textLengthProperties) {
                record(maps.lengths, style[property], sample, role);
            }
            const typographyKey = JSON.stringify({
                fontFamily: style.fontFamily,
                fontSize: style.fontSize,
                fontWeight: style.fontWeight,
                lineHeight: style.lineHeight,
                letterSpacing: style.letterSpacing,
                textTransform: style.textTransform,
            });
            record(maps.typography, typographyKey, sample, element.tagName.toLowerCase());
        }
        record(maps.shadows, style.boxShadow, sample, "box-shadow");
        record(maps.shadows, style.textShadow, sample, "text-shadow");

        if (style.backgroundImage && style.backgroundImage !== "none") {
            if (style.backgroundImage.includes("gradient(")) {
                record(maps.gradients, style.backgroundImage, sample, "background");
            }
            addBackgroundAssets(style.backgroundImage, sample);
        }

        if (style.transitionDuration !== "0s" || style.animationDuration !== "0s") {
            const motionKey = JSON.stringify({
                transitionDuration: style.transitionDuration,
                transitionTimingFunction: style.transitionTimingFunction,
                transitionProperty: style.transitionProperty,
                animationName: style.animationName,
                animationDuration: style.animationDuration,
                animationTimingFunction: style.animationTimingFunction,
            });
            record(maps.motion, motionKey, sample, "motion");
        }

        const tag = element.tagName.toLowerCase();
        const role = element.getAttribute("role") || "";
        const isCandidate =
            ["a", "button", "input", "select", "textarea", "nav", "header", "footer", "section", "article"].includes(
                tag,
            ) || Boolean(role);
        if (isCandidate && componentCandidates.length < 500) {
            componentCandidates.push({
                sample,
                tag,
                role,
                text: compactText(element),
                rect: {
                    x: Math.round(rect.x),
                    y: Math.round(rect.y + window.scrollY),
                    width: Math.round(rect.width),
                    height: Math.round(rect.height),
                },
                style: {
                    color: style.color,
                    backgroundColor: style.backgroundColor,
                    border: style.border,
                    borderRadius: style.borderRadius,
                    boxShadow: style.boxShadow,
                    fontFamily: style.fontFamily,
                    fontSize: style.fontSize,
                    fontWeight: style.fontWeight,
                    lineHeight: style.lineHeight,
                    padding: style.padding,
                },
            });
        }

        if (element instanceof HTMLImageElement) {
            recordAsset(element.currentSrc || element.src, "image", sample);
        } else if (element instanceof HTMLVideoElement) {
            recordAsset(element.poster, "video-poster", sample);
        } else if (element instanceof HTMLSourceElement) {
            recordAsset(element.src, "source", sample);
        } else if (element instanceof HTMLCanvasElement) {
            canvasElements += 1;
        } else if (element instanceof SVGElement && tag === "svg") {
            svgElements += 1;
        }

        if (carriesText) {
            textElements += 1;
            const background = effectiveBackground(element);
            const contrastKey = JSON.stringify({
                foreground: style.color,
                background: background.color,
                backgroundImage: background.image !== "none",
                fontSize: style.fontSize,
                fontWeight: style.fontWeight,
                likelyDecorative: likelyDecorativeText,
            });
            record(maps.contrastPairs, contrastKey, sample, likelyDecorativeText ? "decorative-text" : "text");
        }

        for (const pseudo of ["::before", "::after"]) {
            const pseudoStyle = getComputedStyle(element, pseudo);
            if (
                pseudoStyle.content &&
                pseudoStyle.content !== "none" &&
                pseudoStyle.content !== "normal" &&
                pseudoStyle.display !== "none"
            ) {
                pseudoElements += 1;
                if (!isTransparent(pseudoStyle.color)) {
                    record(maps.colors, pseudoStyle.color, `${sample}${pseudo}`, "pseudo-text");
                }
                if (!isTransparent(pseudoStyle.backgroundColor)) {
                    record(maps.colors, pseudoStyle.backgroundColor, `${sample}${pseudo}`, "pseudo-background");
                }
            }
        }
    }

    let accessibleStyleSheets = 0;
    let inaccessibleStyleSheets = 0;
    let cssRuleCount = 0;

    function inspectStyleDeclaration(style, source) {
        for (const property of style) {
            if (!property.startsWith("--")) {
                continue;
            }
            const value = style.getPropertyValue(property).trim();
            record(maps.cssVariables, `${property}\u0000${value}`, source, "custom-property");
        }
    }

    function inspectRules(rules, source) {
        for (const rule of rules) {
            cssRuleCount += 1;
            if (rule.media?.mediaText) {
                mediaQueries.add(rule.media.mediaText);
            } else if (typeof rule.conditionText === "string" && rule.conditionText) {
                mediaQueries.add(rule.conditionText);
            }
            if (rule.style) {
                inspectStyleDeclaration(rule.style, source);
            }
            if (rule.cssRules) {
                inspectRules(rule.cssRules, source);
            }
        }
    }

    for (const styleSheet of document.styleSheets) {
        const source = styleSheet.href || "inline-style";
        try {
            inspectRules(styleSheet.cssRules, source);
            accessibleStyleSheets += 1;
        } catch {
            inaccessibleStyleSheets += 1;
        }
    }

    for (const element of nodes) {
        if (element.hasAttribute("style")) {
            inspectStyleDeclaration(element.style, sampleLabel(element));
        }
    }

    const declaredVariableNames = new Set(sortedValues(maps.cssVariables).map((item) => item.value.split("\u0000", 1)[0]));
    function computedVariablesFor(element) {
        if (!element) {
            return [];
        }
        const style = getComputedStyle(element);
        return Array.from(declaredVariableNames)
            .map((name) => ({ name, value: style.getPropertyValue(name).trim() }))
            .filter((item) => item.value)
            .sort((left, right) => left.name.localeCompare(right.name));
    }
    const computedVariables = {
        html: computedVariablesFor(document.documentElement),
        body: computedVariablesFor(document.body),
        main: computedVariablesFor(document.querySelector("main")),
    };

    const fonts = document.fonts
        ? Array.from(document.fonts).map((font) => ({
              family: font.family,
              style: font.style,
              weight: font.weight,
              stretch: font.stretch,
              status: font.status,
          }))
        : [];
    const frames = Array.from(document.querySelectorAll("iframe")).map((frame) => ({
        src: frame.src || "inline",
        sameOriginReadable: Boolean(frame.contentDocument),
        title: frame.title || "",
    }));

    function sortedValues(map) {
        return Array.from(map.values()).sort((left, right) => right.count - left.count || left.value.localeCompare(right.value));
    }

    function rootStyleSnapshot(element) {
        if (!element) {
            return null;
        }
        const style = getComputedStyle(element);
        return {
            color: style.color,
            backgroundColor: style.backgroundColor,
            backgroundImage: style.backgroundImage,
            fontFamily: style.fontFamily,
            fontSize: style.fontSize,
            lineHeight: style.lineHeight,
        };
    }

    return {
        metadata: {
            title: document.title,
            url: location.href,
            language: document.documentElement.lang || "",
            viewport: { width: window.innerWidth, height: window.innerHeight, devicePixelRatio: window.devicePixelRatio },
            runtime: {
                userAgent: navigator.userAgent,
                navigatorLanguage: navigator.language,
                navigatorLanguages: Array.from(navigator.languages || []),
                timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
                colorScheme: matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light",
                reducedMotion: matchMedia("(prefers-reduced-motion: reduce)").matches ? "reduce" : "no-preference",
            },
            document: {
                width: Math.max(document.documentElement.scrollWidth, document.body?.scrollWidth || 0),
                height: Math.max(document.documentElement.scrollHeight, document.body?.scrollHeight || 0),
            },
            rootStyles: {
                html: rootStyleSnapshot(document.documentElement),
                body: rootStyleSnapshot(document.body),
                main: rootStyleSnapshot(document.querySelector("main")),
            },
        },
        coverage: {
            discoveredElements: nodes.length,
            truncated: stack.length > 0,
            visibleElements,
            hiddenElements,
            textElements,
            pseudoElements,
            openShadowRoots,
            closedShadowRoots: "not-observable",
            canvasElements,
            svgElements,
            styleSheets: document.styleSheets.length,
            accessibleStyleSheets,
            inaccessibleStyleSheets,
            cssRuleCount,
            frames: frames.length,
            crossOriginOrUnreadableFrames: frames.filter((frame) => !frame.sameOriginReadable).length,
        },
        colors: sortedValues(maps.colors),
        lengths: sortedValues(maps.lengths),
        typography: sortedValues(maps.typography),
        shadows: sortedValues(maps.shadows),
        gradients: sortedValues(maps.gradients),
        motion: sortedValues(maps.motion),
        contrastPairs: sortedValues(maps.contrastPairs),
        cssVariables: sortedValues(maps.cssVariables).map((item) => {
            const separator = item.value.indexOf("\u0000");
            return {
                name: item.value.slice(0, separator),
                value: item.value.slice(separator + 1),
                count: item.count,
                samples: item.samples,
            };
        }),
        computedVariables,
        mediaQueries: Array.from(mediaQueries).sort(),
        fonts,
        assets: Array.from(assets.values()).sort((left, right) => right.count - left.count || left.url.localeCompare(right.url)),
        componentCandidates,
        frames,
    };
}

// Motion is sampled before animation stabilization; all other computed styles are sampled after it.
export function collectMotionEvidence({ maxElements = 20_000, maxSamplesPerValue = 5 } = {}) {
    const records = new Map();
    const nodes = [];
    const stack = [document.documentElement];
    while (stack.length > 0 && nodes.length < maxElements) {
        const node = stack.pop();
        if (!(node instanceof Element)) {
            continue;
        }
        nodes.push(node);
        const children = Array.from(node.children);
        for (let index = children.length - 1; index >= 0; index -= 1) {
            stack.push(children[index]);
        }
        if (node.shadowRoot) {
            const shadowChildren = Array.from(node.shadowRoot.children);
            for (let index = shadowChildren.length - 1; index >= 0; index -= 1) {
                stack.push(shadowChildren[index]);
            }
        }
    }

    function sampleLabel(element) {
        const tag = element.tagName.toLowerCase();
        if (element.id) {
            return `${tag}#${element.id.slice(0, 80)}`;
        }
        const role = element.getAttribute("role");
        const classes = Array.from(element.classList).slice(0, 3).join(".");
        return [tag, role ? `[role=${role}]` : "", classes ? `.${classes}` : ""].join("").slice(0, 160);
    }

    for (const element of nodes) {
        const style = getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        if (
            style.display === "none" ||
            style.visibility === "hidden" ||
            style.visibility === "collapse" ||
            Number(style.opacity) === 0 ||
            rect.width <= 0 ||
            rect.height <= 0 ||
            (style.transitionDuration === "0s" && style.animationDuration === "0s")
        ) {
            continue;
        }
        const value = JSON.stringify({
            transitionDuration: style.transitionDuration,
            transitionTimingFunction: style.transitionTimingFunction,
            transitionProperty: style.transitionProperty,
            animationName: style.animationName,
            animationDuration: style.animationDuration,
            animationTimingFunction: style.animationTimingFunction,
        });
        let record = records.get(value);
        if (!record) {
            record = { value, count: 0, samples: [], roles: { motion: 0 } };
            records.set(value, record);
        }
        record.count += 1;
        record.roles.motion += 1;
        const sample = sampleLabel(element);
        if (record.samples.length < maxSamplesPerValue && !record.samples.includes(sample)) {
            record.samples.push(sample);
        }
    }
    return Array.from(records.values()).sort(
        (left, right) => right.count - left.count || left.value.localeCompare(right.value),
    );
}
