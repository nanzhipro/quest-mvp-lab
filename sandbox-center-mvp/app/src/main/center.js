'use strict';

// Host for the resident `sandbox-center` child process.
//
// The Electron main process is the center's parent (exactly like the reference
// implementation hosts it from its main process); renderers and the per-call
// `sandbox-cli` only ever talk to it over the Unix socket.

const { EventEmitter } = require('node:events');
const fs = require('node:fs');
const net = require('node:net');
const path = require('node:path');
const { spawn } = require('node:child_process');

const READY_TIMEOUT_MS = 15000;

class CenterHost extends EventEmitter {
  constructor({ binary, appHome, policy, workspace, socket, autoGrant = true }) {
    super();
    this.binary = binary;
    this.appHome = appHome;
    this.policy = policy;
    this.workspace = workspace;
    this.socket = socket || path.join(appHome, 'center.sock');
    this.autoGrant = autoGrant;
    this.child = null;
    this.descriptor = null;
    this.stopping = false;
  }

  get running() {
    return Boolean(this.child && this.child.exitCode === null && !this.child.killed);
  }

  async start() {
    fs.mkdirSync(this.appHome, { recursive: true });
    fs.mkdirSync(this.workspace, { recursive: true });
    // Directories the demo policy grants (and auto-grants) access to.
    fs.mkdirSync(path.join(this.appHome, 'cache-demo'), { recursive: true });
    fs.mkdirSync(path.join(this.appHome, 'trash'), { recursive: true });
    fs.mkdirSync(path.join(this.workspace, '.sc-trash'), { recursive: true });

    const args = [
      '--app-home',
      this.appHome,
      '--policy',
      this.policy,
      '--workspace',
      this.workspace,
      '--socket',
      this.socket,
      '--auto-grant',
      String(this.autoGrant),
    ];

    this.child = spawn(this.binary, args, { stdio: ['ignore', 'pipe', 'pipe'] });
    this.child.stderr.setEncoding('utf8');
    this.child.stderr.on('data', (chunk) => {
      for (const line of chunk.split('\n')) {
        if (line.trim()) {
          this.emit('log', line.trim());
        }
      }
    });
    this.child.on('exit', (code, signal) => {
      this.emit('exit', { code, signal, expected: this.stopping });
    });

    this.descriptor = await this.waitForReady(this.child.stdout);
    return this.descriptor;
  }

  waitForReady(stream) {
    return new Promise((resolve, reject) => {
      let buffer = '';
      const timer = setTimeout(() => {
        cleanup();
        reject(new Error('sandbox-center did not report readiness within 15s'));
      }, READY_TIMEOUT_MS);

      const onData = (chunk) => {
        buffer += chunk.toString();
        let newline;
        while ((newline = buffer.indexOf('\n')) >= 0) {
          const line = buffer.slice(0, newline).trim();
          buffer = buffer.slice(newline + 1);
          if (!line) continue;
          try {
            const value = JSON.parse(line);
            if (value.event === 'ready') {
              cleanup();
              resolve(value);
              return;
            }
          } catch {
            // Not the handshake line; keep scanning.
          }
        }
      };
      const onExit = (code) => {
        cleanup();
        reject(new Error(`sandbox-center exited during startup (code ${code})`));
      };
      const cleanup = () => {
        clearTimeout(timer);
        stream.off('data', onData);
        this.child.off('exit', onExit);
      };

      stream.on('data', onData);
      this.child.once('exit', onExit);
    });
  }

  /** One request / one response over the Unix socket. */
  rpc(request, { timeout = 5000 } = {}) {
    return new Promise((resolve, reject) => {
      const socket = net.connect(this.socket);
      let buffer = '';
      const timer = setTimeout(() => {
        socket.destroy();
        reject(new Error(`sandbox-center did not answer ${request.op} within ${timeout}ms`));
      }, timeout);

      socket.setEncoding('utf8');
      socket.on('connect', () => socket.write(`${JSON.stringify(request)}\n`));
      socket.on('data', (chunk) => {
        buffer += chunk;
        const newline = buffer.indexOf('\n');
        if (newline < 0) return;
        clearTimeout(timer);
        const line = buffer.slice(0, newline);
        socket.end();
        try {
          const response = JSON.parse(line);
          if (response.ok) {
            resolve(response.result);
          } else {
            reject(new Error(response.error ? `${response.error.code}: ${response.error.message}` : 'unknown error'));
          }
        } catch (error) {
          reject(error);
        }
      });
      socket.on('error', (error) => {
        clearTimeout(timer);
        reject(error);
      });
    });
  }

  async status() {
    if (!this.running) return null;
    try {
      const ping = await this.rpc({ op: 'ping' });
      return { ...ping, ...this.descriptor };
    } catch {
      return null;
    }
  }

  async stop() {
    if (!this.child || this.child.exitCode !== null) return;
    this.stopping = true;
    await new Promise((resolve) => {
      const timer = setTimeout(() => {
        this.child.kill('SIGKILL');
        resolve();
      }, 3000);
      this.child.once('exit', () => {
        clearTimeout(timer);
        resolve();
      });
      this.child.kill('SIGTERM');
    });
  }
}

module.exports = { CenterHost };
