'use strict';

// Per tool call: spawn `sandbox-cli`, stream its output, and split the
// `SC_EVENT|` lines (structured) from the command output (raw).

const { spawn } = require('node:child_process');
const { EventEmitter } = require('node:events');

const EVENT_PREFIX = 'SC_EVENT|';

class ToolCall extends EventEmitter {
  constructor({ cli, socket, cwd, workspace, command, runId }) {
    super();
    this.cli = cli;
    this.socket = socket;
    this.cwd = cwd;
    this.workspace = workspace;
    this.command = command;
    this.runId = runId;
    this.child = null;
    this.cancelled = false;
  }

  start() {
    return new Promise((resolve, reject) => {
      const args = [
        '--socket',
        this.socket,
        '--cwd',
        this.cwd || this.workspace,
        '--workspace',
        this.workspace,
        '--',
        '/bin/zsh',
        '-c',
        this.command,
      ];
      this.child = spawn(this.cli, args, { stdio: ['ignore', 'pipe', 'pipe'] });

      this.child.stdout.setEncoding('utf8');
      this.child.stdout.on('data', (chunk) =>
        this.emit('output', { runId: this.runId, stream: 'stdout', chunk }),
      );

      let carry = '';
      this.child.stderr.setEncoding('utf8');
      this.child.stderr.on('data', (chunk) => {
        carry += chunk;
        const lines = carry.split('\n');
        carry = lines.pop();
        for (const line of lines) {
          if (line.startsWith(EVENT_PREFIX)) {
            try {
              this.emit('event', { runId: this.runId, event: JSON.parse(line.slice(EVENT_PREFIX.length)) });
            } catch {
              this.emit('output', { runId: this.runId, stream: 'stderr', chunk: `${line}\n` });
            }
          } else {
            this.emit('output', { runId: this.runId, stream: 'stderr', chunk: `${line}\n` });
          }
        }
      });

      this.child.on('error', reject);
      this.child.on('close', (code, signal) => {
        if (carry) {
          this.emit('output', { runId: this.runId, stream: 'stderr', chunk: carry });
        }
        resolve({ runId: this.runId, command: this.command, exitCode: code, signal, cancelled: this.cancelled });
      });
    });
  }

  cancel() {
    this.cancelled = true;
    if (this.child && this.child.exitCode === null) {
      this.child.kill('SIGTERM');
      setTimeout(() => {
        if (this.child.exitCode === null) this.child.kill('SIGKILL');
      }, 1500).unref();
    }
  }
}

module.exports = { ToolCall, EVENT_PREFIX };
