'use strict';

// Headless verification of the whole stack, driven through the *renderer* so the
// preload bridge and IPC layer are covered as well.
//
//   1. Renderer runs every scenario against the live center + sandbox-exec and
//      returns structured observations.
//   2. Main asserts the observations, verifies the audit hash chain with the
//      center binary itself, and captures a screenshot of the console.

const fs = require('node:fs');
const path = require('node:path');
const { spawn } = require('node:child_process');

const SCENARIO_TIMEOUT_MS = 60000;

function verifyAuditChain(centerBinary, auditFile) {
  return new Promise((resolve) => {
    const child = spawn(centerBinary, ['--verify-audit', auditFile], { stdio: ['ignore', 'pipe', 'pipe'] });
    let stdout = '';
    let stderr = '';
    child.stdout.on('data', (chunk) => (stdout += chunk));
    child.stderr.on('data', (chunk) => (stderr += chunk));
    child.on('close', (code) => resolve({ ok: code === 0, code, stdout: stdout.trim(), stderr: stderr.trim() }));
  });
}

function auditFile(appHome) {
  const dir = path.join(appHome, 'audit');
  if (!fs.existsSync(dir)) return null;
  const files = fs
    .readdirSync(dir)
    .filter((name) => name.endsWith('.jsonl'))
    .sort();
  return files.length ? path.join(dir, files[files.length - 1]) : null;
}

async function runSelfTest({ window, center, paths, verifyAuditChain }) {
  const started = Date.now();
  console.log('[selftest] waiting for the renderer…');

  await new Promise((resolve) => {
    if (window.webContents.isLoading()) {
      window.webContents.once('did-finish-load', resolve);
    } else {
      resolve();
    }
  });
  await new Promise((resolve) => setTimeout(resolve, 600));

  let report;
  try {
    report = await Promise.race([
      window.webContents.executeJavaScript('window.__selftest.runAll()', true),
      new Promise((_, reject) =>
        setTimeout(() => reject(new Error(`self-test timed out after ${SCENARIO_TIMEOUT_MS}ms`)), SCENARIO_TIMEOUT_MS),
      ),
    ]);
  } catch (error) {
    console.error(`[selftest] renderer failed: ${error.message}`);
    return 1;
  }

  const failures = report.scenarios.filter((scenario) => !scenario.ok);
  for (const scenario of report.scenarios) {
    const mark = scenario.ok ? 'PASS' : 'FAIL';
    console.log(
      `[selftest] ${mark} ${scenario.id.padEnd(22)} exit=${String(scenario.exitCode).padStart(3)} ` +
        `events=${JSON.stringify(scenario.counts)} ${scenario.detail || ''}`,
    );
  }

  // The scenario verdicts come from the pipeline; this second probe makes sure
  // the console actually rendered what the pipeline reported.
  let ui = null;
  try {
    ui = await window.webContents.executeJavaScript(
      `(() => ({
        scenarioButtons: document.querySelectorAll('.scenario').length,
        verdicts: document.querySelectorAll('.scenario .verdict').length,
        auditRows: document.querySelectorAll('#audit-body tr').length,
        eventItems: document.querySelectorAll('#events li').length,
        terminalChars: document.getElementById('terminal').textContent.length,
        centerPill: document.getElementById('center-pill').textContent,
        policyRows: document.querySelectorAll('#policy dt').length,
        workspace: document.getElementById('terminal').textContent.includes('/workspace'),
      }))()`,
      true,
    );
  } catch (error) {
    console.error(`[selftest] renderer probe failed: ${error.message}`);
  }
  const uiChecks = [
    ['scenario buttons', ui && ui.scenarioButtons === report.scenarios.length],
    ['scenario verdicts', ui && ui.verdicts === report.scenarios.length],
    ['audit rows rendered', ui && ui.auditRows >= 5],
    ['terminal rendered output', ui && ui.terminalChars > 200],
    ['findings rendered', ui && ui.eventItems > 0],
    ['center pill running', ui && /running/.test(ui.centerPill)],
    ['effective policy rendered', ui && ui.policyRows >= 5],
  ];
  for (const [label, ok] of uiChecks) {
    console.log(`[selftest] ${ok ? 'PASS' : 'FAIL'} ui: ${label}${ui ? ` (${label === 'center pill running' ? ui.centerPill : ''})` : ''}`);
  }
  const uiOk = uiChecks.every(([, ok]) => ok);

  const file = auditFile(paths.appHome);
  const audit = file ? await verifyAuditChain(paths.centerBinary, file) : { ok: false, stderr: 'no audit file' };
  console.log(`[selftest] audit chain : ${audit.ok ? 'PASS' : 'FAIL'} ${audit.stdout || audit.stderr}`);

  let screenshot = null;
  try {
    const image = await window.webContents.capturePage();
    const dir = path.join(paths.appHome, 'artifacts');
    fs.mkdirSync(dir, { recursive: true });
    screenshot = path.join(dir, 'selftest-console.png');
    fs.writeFileSync(screenshot, image.toPNG());
    console.log(`[selftest] screenshot  : ${screenshot}`);
  } catch (error) {
    console.error(`[selftest] screenshot failed: ${error.message}`);
  }

  const ping = await center.status();
  const ok = failures.length === 0 && audit.ok && uiOk && Boolean(ping);
  console.log(
    `[selftest] center=${ping ? ping.policy_id : 'unreachable'} rules=${ping ? ping.rules : '?'} ` +
      `scenarios=${report.scenarios.length} failed=${failures.length} ui=${uiOk ? 'ok' : 'fail'} ` +
      `duration=${Date.now() - started}ms`,
  );
  console.log(ok ? '[selftest] RESULT: PASS' : '[selftest] RESULT: FAIL');
  return ok ? 0 : 1;
}

module.exports = { runSelfTest, verifyAuditChain };
