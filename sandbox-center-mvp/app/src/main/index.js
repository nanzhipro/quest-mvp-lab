'use strict';

// Electron main process: owns the window, hosts the resident sandbox-center and
// brokers every tool call to a fresh `sandbox-cli` process.
//
// Process topology (mirrors the reference implementation):
//
//   Electron main ──spawn──> sandbox-center (resident, Unix socket)
//        │
//        ├─ Helper (GPU) / Helper (Renderer) …            ← UI only, sandboxed
//        └─ per tool call: sandbox-cli ──> sandbox-exec ──> zsh ──> python3/node/…
//
// Renderers never touch the binaries: they ask the main process over IPC.

const { app, BrowserWindow, ipcMain, shell } = require('electron');
const fs = require('node:fs');
const path = require('node:path');

const { CenterHost } = require('./center');
const { ToolCall } = require('./runner');
const paths = require('./paths');
const { runSelfTest, verifyAuditChain } = require('./selftest');

const SELF_TEST = process.argv.includes('--self-test');
if (SELF_TEST) {
  // Must run before `app.whenReady()`; a hidden window needs no GPU process.
  app.disableHardwareAcceleration();
}

let mainWindow = null;
let center = null;
let activeCall = null;
let runSequence = 0;
const runs = [];

function emit(channel, payload) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send(channel, payload);
  }
}

async function bootstrap() {
  const appHome = paths.appHome();
  const workspace = paths.workspacePath();
  fs.mkdirSync(appHome, { recursive: true });

  center = new CenterHost({
    binary: paths.centerBinary(),
    appHome,
    policy: paths.policyPath(),
    workspace,
    socket: path.join(appHome, 'center.sock'),
    autoGrant: process.env.SC_AUTO_GRANT !== 'false',
  });
  center.on('log', (line) => {
    console.log(`[center] ${line}`);
    emit('sbx:center-log', line);
  });
  center.on('exit', ({ code, signal, expected }) => {
    emit('sbx:status-changed', { running: false, code, signal });
    if (!expected) {
      console.error(`sandbox-center exited unexpectedly (code=${code} signal=${signal})`);
    }
  });

  const descriptor = await center.start();
  console.log(`[main] sandbox-center ready: socket=${descriptor.socket} policy=${descriptor.policy_id}`);
  return { descriptor, appHome, workspace };
}

function registerIpc({ appHome, workspace }) {
  ipcMain.handle('sbx:paths', () => ({
    appHome,
    workspace,
    policy: paths.policyPath(),
    socket: center.socket,
    centerBinary: paths.centerBinary(),
    cliBinary: paths.cliBinary(),
    versions: {
      electron: process.versions.electron,
      node: process.versions.node,
      chrome: process.versions.chrome,
    },
  }));

  // During shutdown the renderer may still poll; answer `null` instead of throwing.
  ipcMain.handle('sbx:status', () => (center ? center.status() : null));

  ipcMain.handle('sbx:sessions', async () => {
    if (!center) return [];
    const result = await center.rpc({ op: 'list_sessions' });
    return result.sessions;
  });

  ipcMain.handle('sbx:events', async (_event, limit = 30) => {
    if (!center) return { events: [], total: 0 };
    const result = await center.rpc({ op: 'recent_events', limit });
    return result;
  });

  ipcMain.handle('sbx:cancel', () => {
    if (activeCall) {
      activeCall.cancel();
      return true;
    }
    return false;
  });

  ipcMain.handle('sbx:run', async (_event, payload) => {
    const command = String(payload && payload.command ? payload.command : '').trim();
    if (!command) throw new Error('empty command');
    if (!center) throw new Error('sandbox-center is not running');
    if (activeCall) throw new Error('a tool call is already running');

    const runId = `run-${++runSequence}`;
    const call = new ToolCall({
      cli: paths.cliBinary(),
      socket: center.socket,
      cwd: payload.cwd || workspace,
      workspace,
      command,
      runId,
    });
    activeCall = call;

    const events = [];
    const output = [];
    call.on('output', (message) => {
      output.push(message);
      emit('sbx:output', message);
    });
    call.on('event', (message) => {
      events.push(message.event);
      emit('sbx:event', message);
    });

    emit('sbx:run-started', { runId, command });
    try {
      const summary = await call.start();
      const collect = (stream) =>
        output
          .filter((item) => item.stream === stream)
          .map((item) => item.chunk)
          .join('')
          .slice(0, 64 * 1024);
      const record = {
        ...summary,
        events,
        stdout: collect('stdout'),
        stderr: collect('stderr'),
        outputBytes: output.reduce((total, item) => total + item.chunk.length, 0),
      };
      runs.push(record);
      emit('sbx:run-finished', record);
      return record;
    } finally {
      activeCall = null;
    }
  });

  ipcMain.handle('sbx:open-app-home', async () => {
    await shell.openPath(appHome);
    return true;
  });
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1360,
    height: 880,
    minWidth: 1080,
    minHeight: 700,
    show: !SELF_TEST,
    backgroundColor: '#0b0e14',
    title: 'sandbox-center MVP',
    webPreferences: {
      preload: path.join(__dirname, '..', 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      spellcheck: false,
    },
  });
  mainWindow.loadFile(path.join(__dirname, '..', 'renderer', 'index.html'), {
    query: SELF_TEST ? { selftest: '1' } : {},
  });
  if (SELF_TEST) {
    mainWindow.webContents.on('console-message', (event) => {
      if (event.level === 'error') console.error(`[renderer] ${event.message}`);
    });
  }
  return mainWindow;
}

app.whenReady().then(async () => {
  try {
    const { appHome, workspace } = await bootstrap();
    registerIpc({ appHome, workspace });
    const window = createWindow();

    if (SELF_TEST) {
      const exitCode = await runSelfTest({
        window,
        center,
        paths: { appHome, workspace, centerBinary: paths.centerBinary() },
        verifyAuditChain,
      });
      await center.stop();
      app.exit(exitCode);
    }
  } catch (error) {
    console.error(`[main] fatal: ${error && error.stack ? error.stack : error}`);
    if (center) await center.stop();
    app.exit(SELF_TEST ? 1 : 0);
  }
});

app.on('window-all-closed', () => {
  app.quit();
});

app.on('before-quit', async (event) => {
  if (center && center.running) {
    event.preventDefault();
    await center.stop();
    center = null;
    app.quit();
  }
});
