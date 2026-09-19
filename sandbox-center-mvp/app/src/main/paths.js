'use strict';

// Path resolution for the shell: the Electron app lives in `app/`, the Rust
// enforcement core in `core/target/<profile>/`.

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const APP_DIR = path.resolve(__dirname, '..', '..');
const PROJECT_ROOT = path.resolve(APP_DIR, '..');
const CORE_TARGET = path.join(PROJECT_ROOT, 'core', 'target');

function binary(name) {
  const override = process.env[name === 'sandbox-center' ? 'SC_CENTER_BIN' : 'SC_CLI_BIN'];
  if (override) {
    return override;
  }
  for (const profile of ['release', 'debug']) {
    const candidate = path.join(CORE_TARGET, profile, name);
    if (fs.existsSync(candidate)) {
      return candidate;
    }
  }
  throw new Error(
    `cannot find the \`${name}\` binary — run \`cargo build --release\` in core/ first ` +
      `(or set SC_${name === 'sandbox-center' ? 'CENTER' : 'CLI'}_BIN)`,
  );
}

function appHome() {
  return process.env.SC_APP_HOME || path.join(os.homedir(), '.sandbox-center-mvp');
}

function policyPath() {
  return (
    process.env.SC_POLICY || path.join(PROJECT_ROOT, 'policy', 'default-policy.json')
  );
}

function workspacePath() {
  return process.env.SC_WORKSPACE || path.join(appHome(), 'workspace');
}

module.exports = {
  APP_DIR,
  PROJECT_ROOT,
  centerBinary: () => binary('sandbox-center'),
  cliBinary: () => binary('sandbox-cli'),
  appHome,
  policyPath,
  workspacePath,
};
