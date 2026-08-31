import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";

export async function pathExists(targetPath) {
    try {
        await fs.access(targetPath);
        return true;
    } catch {
        return false;
    }
}

export async function writeJson(filePath, value) {
    await fs.writeFile(filePath, `${JSON.stringify(value, null, 2)}\n`, "utf8");
}

export async function fileDigest(filePath) {
    const contents = await fs.readFile(filePath);
    return { bytes: contents.length, sha256: crypto.createHash("sha256").update(contents).digest("hex") };
}

export async function listFiles(root, directory = root) {
    const output = [];
    const entries = await fs.readdir(directory, { withFileTypes: true });
    for (const entry of entries.sort((left, right) => left.name.localeCompare(right.name))) {
        const absolute = path.join(directory, entry.name);
        if (entry.isDirectory()) {
            output.push(...(await listFiles(root, absolute)));
        } else if (entry.isFile() && entry.name !== "manifest.json") {
            output.push({
                path: path.relative(root, absolute).split(path.sep).join("/"),
                ...(await fileDigest(absolute)),
            });
        }
    }
    return output;
}
