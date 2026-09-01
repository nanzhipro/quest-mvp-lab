import fs from "node:fs/promises";
import path from "node:path";

import { parseArgs, usage } from "./args.mjs";
import { captureAll, inspectRobots } from "./capture.mjs";
import { listFiles, pathExists, writeJson } from "./files.mjs";
import { buildArtifacts } from "./normalize.mjs";
import { renderDesignMarkdown, renderValidationMarkdown } from "./render.mjs";
import { assertSafeTargetUrl, redactUrl } from "./url-safety.mjs";
import { verifyExtraction } from "./verify.mjs";

export const TOOL_VERSION = "0.4.0";

export async function executeExtraction(options) {
    if (await pathExists(options.outputDir)) {
        throw new Error(`Output directory already exists: ${options.outputDir}`);
    }
    const targetUrl = await assertSafeTargetUrl(options.url, { allowPrivate: options.allowPrivate });
    await fs.mkdir(path.join(options.outputDir, "evidence"), { recursive: true });
    const startedAt = new Date().toISOString();

    try {
        const robots = await inspectRobots(targetUrl, options.timeoutMs, { allowPrivate: options.allowPrivate });
        await fs.writeFile(path.join(options.outputDir, "evidence", "robots.txt"), robots.body ?? "", "utf8");
        await writeJson(path.join(options.outputDir, "evidence", "robots.json"), { ...robots, body: undefined });
        if (!options.ignoreRobots && robots.allowed === false) {
            throw new Error(`robots.txt disallows capture of ${redactUrl(targetUrl.href)}`);
        }

        const captures = await captureAll(targetUrl, options);
        for (const capture of captures) {
            await assertSafeTargetUrl(capture.finalUrl, { allowPrivate: options.allowPrivate });
            delete capture.finalUrl;
        }
        const capturedAt = new Date().toISOString();
        const metadata = {
            title: captures[0]?.evidence.metadata.title ?? "Extracted Web Design System",
            sourceUrl: redactUrl(targetUrl.href),
            capturedAt,
            tool: `web-design-system-extractor/${TOOL_VERSION}`,
            evidencePolicy:
                "rendered-state, multi-viewport, pre-stabilization motion plus animation-stabilized visual tokens, no authenticated browser profile",
        };
        const artifacts = buildArtifacts(captures, metadata);
        await writeJson(path.join(options.outputDir, "raw-inventory.json"), artifacts.raw);
        await writeJson(path.join(options.outputDir, "design.tokens.json"), artifacts.tokens);
        await fs.writeFile(path.join(options.outputDir, "tokens.css"), artifacts.css, "utf8");
        await fs.writeFile(
            path.join(options.outputDir, "DESIGN.md"),
            renderDesignMarkdown({
                raw: artifacts.raw,
                tokens: artifacts.tokens,
                targetUrl: redactUrl(targetUrl.href),
                capturedAt,
                toolVersion: TOOL_VERSION,
            }),
            "utf8",
        );

        const validation = await verifyExtraction(options.outputDir, captures, artifacts.tokens, {
            requireLiveSchema: options.requireLiveSchema,
        });
        await writeJson(path.join(options.outputDir, "validation.json"), validation);
        await fs.writeFile(path.join(options.outputDir, "VALIDATION.md"), renderValidationMarkdown(validation), "utf8");

        const manifest = {
            schemaVersion: 1,
            status: validation.passed ? "PASS" : "FAIL",
            grade: validation.passed ? "complete-for-declared-scope" : "partial",
            startedAt,
            completedAt: new Date().toISOString(),
            sourceUrl: redactUrl(targetUrl.href),
            toolVersion: TOOL_VERSION,
            robots: { ...robots, body: undefined },
            robotsIgnored: options.ignoreRobots,
            captureProfile: {
                ...captures[0]?.runtime,
                viewports: captures.map((capture) => capture.viewport),
            },
            viewports: captures.map((capture) => capture.viewport),
            captureSummary: captures.map((capture) => ({
                viewport: capture.viewport.name,
                finalUrl: capture.evidence.metadata.url,
                document: capture.evidence.metadata.document,
                coverage: capture.evidence.coverage,
                settling: capture.settling,
                screenshot: capture.screenshot,
                mhtml: capture.mhtml,
                network: capture.network,
                runtime: capture.runtime,
            })),
            validation: {
                passed: validation.passed,
                checkCount: validation.checks.length,
                warningCount: validation.warnings.length,
                requireLiveSchema: options.requireLiveSchema,
            },
            files: await listFiles(options.outputDir),
        };
        await writeJson(path.join(options.outputDir, "manifest.json"), manifest);
        if (!validation.passed) {
            throw new Error(`Extraction completed but validation failed; inspect ${path.join(options.outputDir, "VALIDATION.md")}`);
        }
        return { outputDir: options.outputDir, manifest, validation };
    } catch (error) {
        await writeJson(path.join(options.outputDir, "run-error.json"), {
            status: "FAIL",
            startedAt,
            failedAt: new Date().toISOString(),
            sourceUrl: redactUrl(targetUrl.href),
            error: error instanceof Error ? error.message : String(error),
        });
        throw error;
    }
}

export async function runCli(argv) {
    let options;
    try {
        options = parseArgs(argv);
    } catch (error) {
        throw new Error(`${error.message}\n\n${usage()}`);
    }
    if (options.help) {
        process.stdout.write(usage());
        return;
    }
    const result = await executeExtraction(options);
    process.stdout.write(
        `${JSON.stringify(
            {
                status: result.manifest.status,
                grade: result.manifest.grade,
                outputDir: result.outputDir,
                validationChecks: result.validation.checks.length,
                warnings: result.validation.warnings.length,
            },
            null,
            2,
        )}\n`,
    );
}
