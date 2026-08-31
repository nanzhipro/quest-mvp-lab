import crypto from "node:crypto";
import { createRequire } from "node:module";
import fs from "node:fs/promises";
import path from "node:path";
import { chromium } from "playwright";
import robotsParser from "robots-parser";

import { collectMotionEvidence, collectPageEvidence } from "./page-probe.mjs";
import { assertSafeTargetUrl, redactUrl } from "./url-safety.mjs";

const ARCHIVED_RESOURCE_TYPES = new Set(["stylesheet", "image", "font", "media"]);
const require = createRequire(import.meta.url);
const PLAYWRIGHT_VERSION = require("playwright/package.json").version;
const ROBOTS_USER_AGENT = "CodexWebDesignExtractor/0.3";

function sha256(value) {
    return crypto.createHash("sha256").update(value).digest("hex");
}

function extensionFor(contentType, url) {
    const normalized = (contentType || "").split(";", 1)[0].trim().toLowerCase();
    const known = {
        "text/css": ".css",
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "image/avif": ".avif",
        "image/svg+xml": ".svg",
        "font/woff": ".woff",
        "font/woff2": ".woff2",
        "font/ttf": ".ttf",
        "font/otf": ".otf",
        "video/mp4": ".mp4",
        "video/webm": ".webm",
    };
    if (known[normalized]) {
        return known[normalized];
    }
    try {
        const extension = path.extname(new URL(url).pathname).toLowerCase();
        if (/^\.[a-z0-9]{1,8}$/.test(extension)) {
            return extension;
        }
    } catch {
        // Fall through to the binary extension.
    }
    return ".bin";
}

function selectedHeaders(headers) {
    const allowList = [
        "accept-ranges",
        "access-control-allow-origin",
        "cache-control",
        "content-encoding",
        "content-length",
        "content-type",
        "etag",
        "last-modified",
    ];
    return Object.fromEntries(allowList.filter((name) => headers[name] !== undefined).map((name) => [name, headers[name]]));
}

function redactUrlsInText(value) {
    return String(value).replace(/https?:\/\/[^\s"'<>]+/g, (url) => redactUrl(url));
}

function stripSensitiveHeaders(headers = []) {
    const sensitive = /^(?:authorization|cookie|proxy-authorization|set-cookie)$/i;
    return headers.filter((header) => !sensitive.test(header.name));
}

async function sanitizeHar(filePath) {
    let har;
    try {
        har = JSON.parse(await fs.readFile(filePath, "utf8"));
    } catch {
        return;
    }
    for (const entry of har.log?.entries ?? []) {
        entry.request.url = redactUrl(entry.request.url);
        entry.request.headers = stripSensitiveHeaders(entry.request.headers);
        entry.request.cookies = [];
        entry.request.queryString = (entry.request.queryString ?? []).map((item) => ({
            name: item.name,
            value: /(?:access|api|auth|code|credential|key|password|secret|session|signature|token)/i.test(item.name)
                ? "REDACTED"
                : "VALUE",
        }));
        delete entry.request.postData;
        entry.response.headers = stripSensitiveHeaders(entry.response.headers);
        entry.response.cookies = [];
        if (entry.response.redirectURL) {
            entry.response.redirectURL = redactUrl(entry.response.redirectURL);
        }
    }
    await fs.writeFile(filePath, `${JSON.stringify(har, null, 2)}\n`, "utf8");
}

async function withTimeout(promise, timeoutMs, label) {
    let timer;
    try {
        return await Promise.race([
            promise,
            new Promise((_, reject) => {
                timer = setTimeout(() => reject(new Error(`${label} timed out after ${timeoutMs}ms`)), timeoutMs);
            }),
        ]);
    } finally {
        clearTimeout(timer);
    }
}

async function settlePage(page, timeoutMs) {
    const diagnostics = {
        networkIdleReached: false,
        fontsReady: false,
        imagesDecoded: false,
        autoScroll: null,
    };

    try {
        await page.waitForLoadState("networkidle", { timeout: Math.min(timeoutMs, 8_000) });
        diagnostics.networkIdleReached = true;
    } catch {
        // Long polling and analytics commonly prevent networkidle. Later gates still run.
    }

    try {
        await withTimeout(page.evaluate(() => document.fonts?.ready), Math.min(timeoutMs, 10_000), "font readiness");
        diagnostics.fontsReady = true;
    } catch {
        // Font status is also captured from document.fonts for later review.
    }

    diagnostics.autoScroll = await page.evaluate(async ({ maximumDurationMs }) => {
        const startedAt = performance.now();
        const pause = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
        let previousHeight = 0;
        let stableRounds = 0;
        let steps = 0;
        while (performance.now() - startedAt < maximumDurationMs && stableRounds < 3) {
            const height = Math.max(document.documentElement.scrollHeight, document.body?.scrollHeight || 0);
            const viewport = window.innerHeight;
            for (let y = 0; y < height; y += Math.max(240, Math.floor(viewport * 0.8))) {
                window.scrollTo(0, y);
                steps += 1;
                await pause(70);
                if (performance.now() - startedAt >= maximumDurationMs) {
                    break;
                }
            }
            window.scrollTo(0, height);
            await pause(250);
            const nextHeight = Math.max(document.documentElement.scrollHeight, document.body?.scrollHeight || 0);
            stableRounds = nextHeight === previousHeight ? stableRounds + 1 : 0;
            previousHeight = nextHeight;
        }
        window.scrollTo(0, 0);
        await pause(150);
        return {
            durationMs: Math.round(performance.now() - startedAt),
            steps,
            finalHeight: Math.max(document.documentElement.scrollHeight, document.body?.scrollHeight || 0),
            stable: stableRounds >= 3,
        };
    }, { maximumDurationMs: Math.min(timeoutMs, 20_000) });

    try {
        await withTimeout(
            page.evaluate(async () => {
                const images = Array.from(document.images).filter((image) => image.currentSrc || image.src);
                await Promise.all(
                    images.map(async (image) => {
                        if (!image.complete) {
                            await new Promise((resolve) => {
                                image.addEventListener("load", resolve, { once: true });
                                image.addEventListener("error", resolve, { once: true });
                            });
                        }
                        if (typeof image.decode === "function") {
                            await image.decode().catch(() => {});
                        }
                    }),
                );
            }),
            Math.min(timeoutMs, 12_000),
            "image decoding",
        );
        diagnostics.imagesDecoded = true;
    } catch {
        // Failed image URLs remain visible in the network and DOM evidence.
    }
    return diagnostics;
}

async function captureScreenshot(page, directory, documentSize) {
    const maxFullPageHeight = 30_000;
    const maxFullPagePixels = 100_000_000;
    if (documentSize.height <= maxFullPageHeight && documentSize.width * documentSize.height <= maxFullPagePixels) {
        const screenshotPath = path.join(directory, "screenshot.png");
        await page.screenshot({ path: screenshotPath, fullPage: true, animations: "disabled", caret: "hide" });
        return { mode: "full-page", files: ["screenshot.png"] };
    }

    const files = [];
    const tileHeight = 8_000;
    for (let y = 0, index = 1; y < documentSize.height; y += tileHeight, index += 1) {
        const filename = `screenshot-tile-${String(index).padStart(3, "0")}.png`;
        const height = Math.min(tileHeight, documentSize.height - y);
        await page.screenshot({
            path: path.join(directory, filename),
            clip: { x: 0, y, width: documentSize.width, height },
            animations: "disabled",
            caret: "hide",
        });
        files.push(filename);
    }
    return { mode: "tiled", files, tileHeight };
}

async function installRequestSafety(context, options, blockedRequests) {
    if (options.allowPrivate) {
        return;
    }
    const approvedOrigins = new Map();
    await context.route("**/*", async (route) => {
        const request = route.request();
        const requestUrl = request.url();
        if (!/^https?:/i.test(requestUrl)) {
            await route.continue();
            return;
        }
        try {
            const origin = new URL(requestUrl).origin;
            let validation;
            if (request.isNavigationRequest()) {
                validation = assertSafeTargetUrl(requestUrl);
            } else {
                validation = approvedOrigins.get(origin);
                if (!validation) {
                    validation = assertSafeTargetUrl(requestUrl);
                    approvedOrigins.set(origin, validation);
                }
            }
            await validation;
            await route.continue();
        } catch (error) {
            if (blockedRequests.length < 1_000) {
                blockedRequests.push({
                    url: redactUrl(requestUrl),
                    resourceType: request.resourceType(),
                    reason: error.message,
                });
            }
            await route.abort("blockedbyclient");
        }
    });
}

async function captureViewport(browser, targetUrl, viewport, options) {
    const directory = path.join(options.outputDir, "evidence", viewport.name);
    const assetDirectory = path.join(directory, "assets");
    await fs.mkdir(assetDirectory, { recursive: true });

    const harPath = path.join(directory, "network.har");
    const context = await browser.newContext({
        viewport: { width: viewport.width, height: viewport.height },
        deviceScaleFactor: 1,
        locale: options.locale,
        timezoneId: options.timezoneId,
        colorScheme: options.colorScheme,
        reducedMotion: options.reducedMotion,
        serviceWorkers: "block",
        ignoreHTTPSErrors: false,
        recordHar: { path: harPath, mode: "full", content: "omit" },
    });
    const consoleErrors = [];
    const failedRequests = [];
    const blockedRequests = [];
    const resources = [];
    const archivedUrls = new Set();
    const archiveTasks = [];
    let archiveClosed = false;
    let archivedBytes = 0;
    await installRequestSafety(context, options, blockedRequests);
    const page = await context.newPage();

    page.on("console", (message) => {
        if (message.type() === "error" && consoleErrors.length < 200) {
            consoleErrors.push(redactUrlsInText(message.text()).slice(0, 2_000));
        }
    });
    page.on("pageerror", (error) => {
        if (consoleErrors.length < 200) {
            consoleErrors.push(redactUrlsInText(error.message).slice(0, 2_000));
        }
    });
    page.on("requestfailed", (request) => {
        if (failedRequests.length < 1_000) {
            failedRequests.push({
                url: redactUrl(request.url()),
                method: request.method(),
                resourceType: request.resourceType(),
                failure: request.failure()?.errorText ?? "unknown",
            });
        }
    });
    page.on("response", (response) => {
        if (archiveClosed) {
            return;
        }
        const task = (async () => {
            const request = response.request();
            const resourceType = request.resourceType();
            const rawUrl = response.url();
            const headers = response.headers();
            const record = {
                url: redactUrl(rawUrl),
                urlSha256: sha256(rawUrl),
                method: request.method(),
                resourceType,
                status: response.status(),
                fromServiceWorker: response.fromServiceWorker(),
                headers: selectedHeaders(headers),
                archived: false,
            };
            resources.push(record);

            if (
                !ARCHIVED_RESOURCE_TYPES.has(resourceType) ||
                response.status() < 200 ||
                response.status() >= 300 ||
                archivedUrls.has(rawUrl)
            ) {
                return;
            }
            archivedUrls.add(rawUrl);
            const statedLength = Number(headers["content-length"] || 0);
            if (statedLength > options.maxResourceBytes) {
                record.archiveSkipReason = "content-length-exceeds-per-resource-limit";
                return;
            }

            try {
                const body = await withTimeout(response.body(), Math.min(options.timeoutMs, 15_000), "resource body");
                if (body.length > options.maxResourceBytes) {
                    record.archiveSkipReason = "body-exceeds-per-resource-limit";
                    return;
                }
                if (archivedBytes + body.length > options.maxTotalBytes) {
                    record.archiveSkipReason = "viewport-archive-budget-exhausted";
                    return;
                }
                archivedBytes += body.length;
                const extension = extensionFor(headers["content-type"], rawUrl);
                const filename = `${sha256(rawUrl).slice(0, 24)}${extension}`;
                await fs.writeFile(path.join(assetDirectory, filename), body);
                record.archived = true;
                record.archivePath = `assets/${filename}`;
                record.bodyBytes = body.length;
                record.bodySha256 = sha256(body);
            } catch (error) {
                record.archiveSkipReason = `body-unavailable:${error.message}`;
            }
        })();
        archiveTasks.push(task);
    });

    let capture;
    try {
        const response = await page.goto(targetUrl.href, { waitUntil: "domcontentloaded", timeout: options.timeoutMs });
        if (!response) {
            throw new Error("Navigation produced no main-document response");
        }
        if (response.status() >= 400) {
            throw new Error(`Main document returned HTTP ${response.status()}`);
        }

        const settling = await settlePage(page, options.timeoutMs);
        const motion = await page.evaluate(collectMotionEvidence, {
            maxElements: options.maxElements,
            maxSamplesPerValue: 5,
        });
        const finalUrl = page.url();

        await fs.writeFile(path.join(directory, "rendered-dom.html"), await page.content(), "utf8");

        let mhtml = { captured: false };
        try {
            const cdp = await context.newCDPSession(page);
            const snapshot = await cdp.send("Page.captureSnapshot", { format: "mhtml" });
            await fs.writeFile(path.join(directory, "page.mhtml"), snapshot.data, "utf8");
            mhtml = { captured: true, bytes: Buffer.byteLength(snapshot.data), sha256: sha256(snapshot.data) };
            await cdp.detach();
        } catch (error) {
            mhtml = { captured: false, error: error.message };
        }

        await page.addStyleTag({
            content:
                "*,*::before,*::after{animation-delay:0s!important;animation-duration:0s!important;" +
                "transition-delay:0s!important;transition-duration:0s!important;caret-color:transparent!important}",
        });
        await page.evaluate(() => window.scrollTo(0, 0));
        await page.waitForTimeout(150);
        settling.animationsStabilized = true;
        const evidence = await page.evaluate(collectPageEvidence, {
            maxElements: options.maxElements,
            maxSamplesPerValue: 5,
        });
        evidence.motion = motion;
        evidence.metadata.url = redactUrl(evidence.metadata.url);
        evidence.assets = evidence.assets.map((asset) => ({ ...asset, url: redactUrl(asset.url) }));
        evidence.frames = evidence.frames.map((frame) => ({ ...frame, src: redactUrl(frame.src) }));
        await fs.writeFile(path.join(directory, "visual-evidence.json"), `${JSON.stringify(evidence, null, 2)}\n`, "utf8");
        const screenshot = await captureScreenshot(page, directory, evidence.metadata.document);

        archiveClosed = true;
        await Promise.allSettled(archiveTasks);
        resources.sort((left, right) => left.url.localeCompare(right.url) || left.status - right.status);
        const network = {
            resources,
            failedRequests,
            blockedRequests,
            consoleErrors,
            archivedBytes,
            archivedResourceCount: resources.filter((resource) => resource.archived).length,
            totalResourceCount: resources.length,
        };
        await fs.writeFile(path.join(directory, "resources.json"), `${JSON.stringify(network, null, 2)}\n`, "utf8");

        capture = {
            viewport,
            finalUrl,
            evidence,
            settling,
            screenshot,
            mhtml,
            network: {
                totalResourceCount: network.totalResourceCount,
                archivedResourceCount: network.archivedResourceCount,
                archivedBytes,
                failedRequestCount: failedRequests.length,
                blockedRequestCount: blockedRequests.length,
                consoleErrorCount: consoleErrors.length,
            },
        };
    } finally {
        archiveClosed = true;
        await Promise.allSettled(archiveTasks);
        await context.close();
    }
    await sanitizeHar(harPath);
    return capture;
}

export async function inspectRobots(targetUrl, timeoutMs, { allowPrivate = false } = {}) {
    const robotsUrl = new URL("/robots.txt", targetUrl.origin);
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), Math.min(timeoutMs, 10_000));
    try {
        let currentUrl = robotsUrl;
        for (let redirects = 0; redirects <= 10; redirects += 1) {
            await assertSafeTargetUrl(currentUrl.href, { allowPrivate });
            const response = await fetch(currentUrl, {
                signal: controller.signal,
                headers: { "user-agent": ROBOTS_USER_AGENT },
                redirect: "manual",
            });
            if (response.status >= 300 && response.status < 400) {
                const location = response.headers.get("location");
                if (!location) {
                    throw new Error(`robots.txt redirect returned HTTP ${response.status} without a Location header`);
                }
                currentUrl = new URL(location, currentUrl);
                continue;
            }
            const body = await response.text();
            let allowed = true;
            let reason = "unavailable";
            if (response.ok) {
                const parser = robotsParser(response.url || currentUrl.href, body);
                allowed = parser.isAllowed(targetUrl.href, ROBOTS_USER_AGENT) !== false;
                reason = allowed ? "allowed" : "disallowed";
            } else if (response.status === 401 || response.status === 403 || response.status >= 500) {
                allowed = false;
                reason = response.status >= 500 ? "unreachable" : "forbidden";
            } else if (response.status === 404 || response.status === 410) {
                reason = "absent";
            }
            return {
                url: redactUrl(response.url),
                status: response.status,
                fetched: response.ok,
                allowed,
                reason,
                body: body.slice(0, 1_000_000),
                truncated: body.length > 1_000_000,
                userAgent: ROBOTS_USER_AGENT,
                note: "robots.txt is enforced by default but is not authorization for private or restricted content.",
            };
        }
        throw new Error("robots.txt exceeded 10 redirects");
    } catch (error) {
        return {
            url: robotsUrl.href,
            fetched: false,
            allowed: true,
            reason: "check-failed",
            error: error.message,
            userAgent: ROBOTS_USER_AGENT,
        };
    } finally {
        clearTimeout(timer);
    }
}

export async function captureAll(targetUrl, options) {
    const browser = await chromium.launch({ headless: !options.headed });
    try {
        const captures = [];
        for (const viewport of options.viewports) {
            const capture = await captureViewport(browser, targetUrl, viewport, options);
            capture.runtime = {
                nodeVersion: process.version,
                playwrightVersion: PLAYWRIGHT_VERSION,
                chromiumVersion: browser.version(),
                locale: options.locale,
                timezoneId: options.timezoneId,
                colorScheme: options.colorScheme,
                reducedMotion: options.reducedMotion,
                deviceScaleFactor: 1,
                serviceWorkers: "block",
            };
            captures.push(capture);
        }
        return captures;
    } finally {
        await browser.close();
    }
}
