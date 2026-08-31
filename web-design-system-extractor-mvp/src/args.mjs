import path from "node:path";

export const DEFAULT_VIEWPORTS = [
    { name: "desktop", width: 1440, height: 900 },
    { name: "mobile", width: 390, height: 844 },
];

export function parseViewportSpec(value) {
    if (!value) {
        return DEFAULT_VIEWPORTS;
    }
    const seen = new Set();
    return value.split(",").map((entry) => {
        const match = /^([a-z][a-z0-9-]{0,31}):(\d{2,5})x(\d{2,5})$/i.exec(entry.trim());
        if (!match) {
            throw new Error(`Invalid viewport '${entry}'. Expected name:WIDTHxHEIGHT`);
        }
        const [, name, widthText, heightText] = match;
        const width = Number(widthText);
        const height = Number(heightText);
        if (width < 240 || width > 7680 || height < 240 || height > 7680) {
            throw new Error(`Viewport '${entry}' is outside the supported 240..7680 pixel range`);
        }
        if (seen.has(name)) {
            throw new Error(`Duplicate viewport name: ${name}`);
        }
        seen.add(name);
        return { name, width, height };
    });
}

export function usage() {
    return `Usage:
  web-ds-extract <url> --out <directory> [options]

Options:
  --viewports <spec>          Comma-separated name:WIDTHxHEIGHT entries
                              (default: desktop:1440x900,mobile:390x844)
  --timeout-ms <number>       Navigation timeout in milliseconds (default: 45000)
  --color-scheme <value>      light or dark (default: light)
  --locale <value>            Browser locale (default: en-US)
  --timezone <value>          IANA timezone (default: UTC)
  --reduced-motion <value>    no-preference or reduce (default: no-preference)
  --max-elements <number>     Maximum rendered elements to inspect (default: 20000)
  --max-resource-bytes <n>    Per visual-resource archive limit (default: 10485760)
  --max-total-bytes <n>       Per-viewport archive limit (default: 104857600)
  --require-live-schema       Require the live DTCG 2025.10 JSON Schema gate
  --ignore-robots             Ignore robots.txt only with explicit authorization
  --allow-private             Permit localhost/private targets for authorized tests
  --headed                    Run Chromium with a visible window
  --help                      Show this help
`;
}

function readValue(argv, index, flag) {
    const value = argv[index + 1];
    if (!value || value.startsWith("--")) {
        throw new Error(`${flag} requires a value`);
    }
    return value;
}

function parseInteger(value, flag, { min, max }) {
    const parsed = Number(value);
    if (!Number.isSafeInteger(parsed) || parsed < min || parsed > max) {
        throw new Error(`${flag} must be an integer in ${min}..${max}`);
    }
    return parsed;
}

export function parseArgs(argv, { cwd = process.cwd() } = {}) {
    if (argv.includes("--help") || argv.includes("-h")) {
        return { help: true };
    }

    const positional = [];
    const options = {
        timeoutMs: 45_000,
        colorScheme: "light",
        locale: "en-US",
        timezoneId: "UTC",
        reducedMotion: "no-preference",
        maxElements: 20_000,
        maxResourceBytes: 10 * 1024 * 1024,
        maxTotalBytes: 100 * 1024 * 1024,
        allowPrivate: false,
        ignoreRobots: false,
        requireLiveSchema: false,
        headed: false,
    };

    for (let index = 0; index < argv.length; index += 1) {
        const argument = argv[index];
        if (!argument.startsWith("--")) {
            positional.push(argument);
            continue;
        }
        switch (argument) {
            case "--out":
                options.outputDir = path.resolve(cwd, readValue(argv, index, argument));
                index += 1;
                break;
            case "--viewports":
                options.viewports = parseViewportSpec(readValue(argv, index, argument));
                index += 1;
                break;
            case "--timeout-ms":
                options.timeoutMs = parseInteger(readValue(argv, index, argument), argument, {
                    min: 1_000,
                    max: 300_000,
                });
                index += 1;
                break;
            case "--color-scheme":
                options.colorScheme = readValue(argv, index, argument);
                if (!new Set(["light", "dark"]).has(options.colorScheme)) {
                    throw new Error("--color-scheme must be light or dark");
                }
                index += 1;
                break;
            case "--locale":
                options.locale = readValue(argv, index, argument);
                index += 1;
                break;
            case "--timezone":
                options.timezoneId = readValue(argv, index, argument);
                index += 1;
                break;
            case "--reduced-motion":
                options.reducedMotion = readValue(argv, index, argument);
                if (!new Set(["no-preference", "reduce"]).has(options.reducedMotion)) {
                    throw new Error("--reduced-motion must be no-preference or reduce");
                }
                index += 1;
                break;
            case "--max-elements":
                options.maxElements = parseInteger(readValue(argv, index, argument), argument, {
                    min: 100,
                    max: 200_000,
                });
                index += 1;
                break;
            case "--max-resource-bytes":
                options.maxResourceBytes = parseInteger(readValue(argv, index, argument), argument, {
                    min: 1024,
                    max: 1024 * 1024 * 1024,
                });
                index += 1;
                break;
            case "--max-total-bytes":
                options.maxTotalBytes = parseInteger(readValue(argv, index, argument), argument, {
                    min: 1024,
                    max: 4 * 1024 * 1024 * 1024,
                });
                index += 1;
                break;
            case "--allow-private":
                options.allowPrivate = true;
                break;
            case "--ignore-robots":
                options.ignoreRobots = true;
                break;
            case "--require-live-schema":
                options.requireLiveSchema = true;
                break;
            case "--headed":
                options.headed = true;
                break;
            default:
                throw new Error(`Unknown option: ${argument}`);
        }
    }

    if (positional.length !== 1) {
        throw new Error("Exactly one URL is required");
    }
    if (!options.outputDir) {
        throw new Error("--out is required so evidence is never written to an ambiguous location");
    }
    options.url = positional[0];
    options.viewports ??= DEFAULT_VIEWPORTS;
    return options;
}
