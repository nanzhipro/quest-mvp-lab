import fs from "node:fs/promises";
import path from "node:path";

import { listFiles, writeJson } from "./files.mjs";
import { renderValidationMarkdown } from "./render.mjs";
import { verifyExtraction } from "./verify.mjs";

function parseValidationArgs(argv) {
    const positional = argv.filter((argument) => !argument.startsWith("--"));
    const unknown = argv.filter(
        (argument) => argument.startsWith("--") && argument !== "--require-live-schema" && argument !== "--help" && argument !== "-h",
    );
    if (unknown.length > 0) {
        throw new Error(`Unknown option: ${unknown[0]}`);
    }
    if (argv.includes("--help") || argv.includes("-h")) {
        return { help: true };
    }
    if (positional.length !== 1) {
        throw new Error("Exactly one extraction output directory is required");
    }
    return {
        outputDir: path.resolve(positional[0]),
        requireLiveSchema: argv.includes("--require-live-schema"),
    };
}

export async function revalidateExtraction(outputDir, { requireLiveSchema = false } = {}) {
    const [manifestText, tokenText] = await Promise.all([
        fs.readFile(path.join(outputDir, "manifest.json"), "utf8"),
        fs.readFile(path.join(outputDir, "design.tokens.json"), "utf8"),
    ]);
    const manifest = JSON.parse(manifestText);
    const tokens = JSON.parse(tokenText);
    const summaries = new Map((manifest.captureSummary ?? []).map((summary) => [summary.viewport, summary]));
    const captures = [];
    for (const viewport of manifest.viewports ?? []) {
        const summary = summaries.get(viewport.name);
        if (!summary) {
            throw new Error(`manifest.json is missing captureSummary for ${viewport.name}`);
        }
        const evidence = JSON.parse(
            await fs.readFile(path.join(outputDir, "evidence", viewport.name, "visual-evidence.json"), "utf8"),
        );
        captures.push({
            viewport,
            evidence,
            settling: summary.settling,
            screenshot: summary.screenshot,
            mhtml: summary.mhtml,
            network: summary.network,
            runtime: summary.runtime,
        });
    }

    const validation = await verifyExtraction(outputDir, captures, tokens, { requireLiveSchema });
    await writeJson(path.join(outputDir, "validation.json"), validation);
    await fs.writeFile(path.join(outputDir, "VALIDATION.md"), renderValidationMarkdown(validation), "utf8");

    manifest.status = validation.passed ? "PASS" : "FAIL";
    manifest.grade = validation.passed ? "complete-for-declared-scope" : "partial";
    manifest.revalidatedAt = new Date().toISOString();
    manifest.validation = {
        passed: validation.passed,
        checkCount: validation.checks.length,
        warningCount: validation.warnings.length,
        requireLiveSchema,
    };
    manifest.files = await listFiles(outputDir);
    await writeJson(path.join(outputDir, "manifest.json"), manifest);
    if (!validation.passed) {
        throw new Error(`Validation failed; inspect ${path.join(outputDir, "VALIDATION.md")}`);
    }
    return { manifest, validation };
}

export async function runValidationCli(argv) {
    const options = parseValidationArgs(argv);
    if (options.help) {
        process.stdout.write(
            "Usage: web-ds-validate <output-directory> [--require-live-schema]\n",
        );
        return;
    }
    const result = await revalidateExtraction(options.outputDir, options);
    process.stdout.write(
        `${JSON.stringify(
            {
                status: result.manifest.status,
                grade: result.manifest.grade,
                outputDir: options.outputDir,
                validationChecks: result.validation.checks.length,
                warnings: result.validation.warnings.length,
            },
            null,
            2,
        )}\n`,
    );
}
