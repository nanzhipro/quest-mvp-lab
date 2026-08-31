import dns from "node:dns/promises";
import net from "node:net";

const SENSITIVE_QUERY_KEY = /(?:access|api|auth|code|credential|key|password|secret|session|signature|token)/i;

export function parseHttpUrl(input) {
    let url;
    try {
        url = new URL(input);
    } catch {
        throw new Error(`Invalid URL: ${input}`);
    }

    if (url.protocol !== "http:" && url.protocol !== "https:") {
        throw new Error(`Only http:// and https:// URLs are supported: ${url.protocol}`);
    }
    if (url.username || url.password) {
        throw new Error("URLs containing embedded credentials are not supported");
    }
    return url;
}

export function isPrivateAddress(address) {
    const ipVersion = net.isIP(address);
    if (ipVersion === 4) {
        const octets = address.split(".").map(Number);
        return (
            octets[0] === 10 ||
            octets[0] === 127 ||
            (octets[0] === 169 && octets[1] === 254) ||
            (octets[0] === 172 && octets[1] >= 16 && octets[1] <= 31) ||
            (octets[0] === 192 && octets[1] === 168) ||
            (octets[0] === 100 && octets[1] >= 64 && octets[1] <= 127) ||
            octets[0] === 0
        );
    }
    if (ipVersion === 6) {
        const normalized = address.toLowerCase();
        return (
            normalized === "::1" ||
            normalized === "::" ||
            normalized.startsWith("fc") ||
            normalized.startsWith("fd") ||
            /^fe[89ab]/.test(normalized) ||
            normalized.startsWith("::ffff:127.") ||
            normalized.startsWith("::ffff:10.") ||
            normalized.startsWith("::ffff:192.168.")
        );
    }
    return false;
}

export function isObviouslyPrivateHostname(hostname) {
    const normalized = hostname.toLowerCase().replace(/\.$/, "");
    return (
        normalized === "localhost" ||
        normalized.endsWith(".localhost") ||
        normalized.endsWith(".local") ||
        isPrivateAddress(normalized)
    );
}

export async function assertSafeTargetUrl(input, { allowPrivate = false } = {}) {
    const url = parseHttpUrl(input);
    if (allowPrivate) {
        return url;
    }
    if (isObviouslyPrivateHostname(url.hostname)) {
        throw new Error("Private and loopback targets require --allow-private");
    }

    let addresses;
    try {
        addresses = await dns.lookup(url.hostname, { all: true, verbatim: true });
    } catch (error) {
        throw new Error(`Unable to resolve ${url.hostname}: ${error.message}`);
    }
    if (addresses.length === 0 || addresses.some(({ address }) => isPrivateAddress(address))) {
        throw new Error("Target resolves to a private or non-routable address; use --allow-private only when authorized");
    }
    return url;
}

export function redactUrl(input) {
    try {
        const url = new URL(input);
        url.hash = "";
        for (const key of url.searchParams.keys()) {
            const replacement = SENSITIVE_QUERY_KEY.test(key) ? "REDACTED" : "VALUE";
            url.searchParams.set(key, replacement);
        }
        return url.toString();
    } catch {
        return "invalid-url";
    }
}
