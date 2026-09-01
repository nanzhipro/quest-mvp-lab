#!/usr/bin/env node
/**
 * Purpose: statically verify the self-contained design-system demo.
 * Usage: node verify.mjs
 */

import { readFile, access, readdir } from "node:fs/promises";
import { constants } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(fileURLToPath(import.meta.url));
const [html, styles, tokens, app] = await Promise.all([
  readFile(join(root, "index.html"), "utf8"),
  readFile(join(root, "styles.css"), "utf8"),
  readFile(join(root, "tokens.css"), "utf8"),
  readFile(join(root, "app.js"), "utf8")
]);

const failures = [];
const checks = [];
const check = (condition, label) => {
  checks.push({ condition, label });
  if (!condition) failures.push(label);
};

const tokenNames = new Set(Array.from(tokens.matchAll(/(--[a-z0-9_-]+)\s*:/gi), (match) => match[1]));
const localNames = new Set(Array.from(styles.matchAll(/(--[a-z0-9_-]+)\s*:/gi), (match) => match[1]));
const usedVariables = new Set(Array.from(`${styles}\n${app}`.matchAll(/var\((--[a-z0-9_-]+)/gi), (match) => match[1]));
const missingVariables = [...usedVariables].filter((name) => !tokenNames.has(name) && !localNames.has(name));

check(tokenNames.size === 88, `Expected 88 extracted CSS tokens, found ${tokenNames.size}`);
check(missingVariables.length === 0, `No unresolved CSS variables (${missingVariables.join(", ") || "none"})`);

const ids = Array.from(html.matchAll(/\sid="([^"]+)"/g), (match) => match[1]);
const duplicateIds = ids.filter((id, index) => ids.indexOf(id) !== index);
check(duplicateIds.length === 0, `HTML IDs are unique (${duplicateIds.join(", ") || "none duplicated"})`);

const screenLabels = Array.from(html.matchAll(/data-screen-label=/g)).length;
check(screenLabels >= 9, `High-level screen labels present (${screenLabels})`);

const requiredSections = ["overview", "color", "type", "geometry", "controls", "feed", "states", "motion", "coverage"];
requiredSections.forEach((id) => check(html.includes(`id="${id}"`), `Section #${id} exists`));

const referencedAssets = Array.from(html.matchAll(/src="(assets\/[^"]+)"/g), (match) => match[1]);
for (const asset of referencedAssets) {
  try {
    await access(join(root, asset), constants.R_OK);
    check(true, `Asset ${asset} is readable`);
  } catch {
    check(false, `Asset ${asset} is readable`);
  }
}

check(observedColorCount(app) === 17, "All 17 observed color tokens are represented");
check(observedGradientCount(app) === 14, "All 14 observed gradients are represented");
check(app.includes("getTokenDeclarations"), "Runtime CSSOM token audit is wired");
check(html.includes("2.91:1"), "Observed orange/white contrast warning is preserved");
check(styles.includes("prefers-reduced-motion: reduce"), "Reduced-motion fallback exists");
check(styles.includes("@media (max-width: 560px)"), "Narrow mobile layout exists");

const projectFiles = await listFiles(root);
const forbiddenTerms = [
  ["长", "亭"].join(""),
  ["薮", "猫"].join(""),
  ["cyber", "serval"].join(""),
  ["chai", "tin"].join(""),
  ["CCLJ", "2GNM3D"].join("")
];
for (const file of projectFiles.filter((path) => /\.(?:html|css|js|mjs|md|json|svg)$/i.test(path))) {
  const content = await readFile(file, "utf8");
  const normalized = content.toLowerCase();
  check(!forbiddenTerms.some((term) => normalized.includes(term.toLowerCase())), `No enterprise-only data in ${file.slice(root.length + 1)}`);
}

for (const item of checks) {
  console.log(`${item.condition ? "PASS" : "FAIL"}  ${item.label}`);
}
console.log(`\n${checks.length - failures.length}/${checks.length} checks passed`);
if (failures.length) process.exitCode = 1;

function observedColorCount(source) {
  const block = source.match(/const observedColors = \[([\s\S]*?)\n\];/);
  return block ? Array.from(block[1].matchAll(/^\s*\[/gm)).length : 0;
}

function observedGradientCount(source) {
  const block = source.match(/const observedGradients = \[([\s\S]*?)\n\];/);
  return block ? Array.from(block[1].matchAll(/^\s*\[/gm)).length : 0;
}

async function listFiles(directory) {
  const output = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) output.push(...await listFiles(path));
    else output.push(path);
  }
  return output;
}
