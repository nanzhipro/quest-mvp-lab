// Renderer: the sandbox console. Everything the user does here becomes
//   renderer → preload → main → sandbox-cli → sandbox-exec → zsh → tool
// and every structured event comes back the same way.

import { buildScenarios, countEvents, evaluate } from './scenarios.js';

const SELF_TEST = new URLSearchParams(window.location.search).get('selftest') === '1';

const state = {
  paths: null,
  scenarios: [],
  running: false,
  results: new Map(),
};

const els = {
  status: document.getElementById('status'),
  centerPill: document.getElementById('center-pill'),
  policyPill: document.getElementById('policy-pill'),
  rulesPill: document.getElementById('rules-pill'),
  scenarioList: document.getElementById('scenario-list'),
  form: document.getElementById('runner-form'),
  input: document.getElementById('command-input'),
  runButton: document.getElementById('run-button'),
  cancelButton: document.getElementById('cancel-button'),
  terminal: document.getElementById('terminal'),
  runState: document.getElementById('run-state'),
  events: document.getElementById('events'),
  policy: document.getElementById('policy'),
  auditBody: document.getElementById('audit-body'),
};

function appendLine(stream, text) {
  const span = document.createElement('span');
  span.className = `line-${stream}`;
  span.textContent = text;
  els.terminal.appendChild(span);
  els.terminal.scrollTop = els.terminal.scrollHeight;
}

function appendMeta(text, kind = 'meta') {
  appendLine(kind === 'ok' ? 'ok' : 'meta', `${text}\n`);
}

function clearTerminal() {
  els.terminal.textContent = '';
}

function renderScenarioList() {
  els.scenarioList.textContent = '';
  for (const scenario of state.scenarios) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'scenario';
    button.dataset.scenario = scenario.id;
    button.disabled = state.running;

    const title = document.createElement('span');
    title.className = 'title';
    title.textContent = scenario.title;

    const command = document.createElement('span');
    command.className = 'cmd';
    command.textContent = scenario.command;

    button.append(title, command);

    const result = state.results.get(scenario.id);
    if (result) {
      const verdict = document.createElement('span');
      verdict.className = `verdict ${result.ok ? 'verdict-ok' : 'verdict-bad'}`;
      verdict.textContent = result.ok
        ? `✔ exit=${result.exitCode} ${JSON.stringify(result.counts)}`
        : `✘ ${result.detail}`;
      button.append(verdict);
    }

    button.addEventListener('click', () => runScenario(scenario));
    els.scenarioList.appendChild(button);
  }
}

function setRunning(running, label = 'idle') {
  state.running = running;
  els.runButton.disabled = running;
  els.cancelButton.disabled = !running;
  els.input.disabled = running;
  els.runState.textContent = label;
  renderScenarioList();
}

function pushEvent(event) {
  const item = document.createElement('li');
  const klass = event.type.replace('.', '-');
  item.className = `ev-${klass.split('-')[0]}`;
  switch (event.type) {
    case 'session.opened':
      item.textContent = `▸ session ${event.session_id} · tag ${event.tag}`;
      renderPolicy(event);
      break;
    case 'violation':
      item.textContent = `✖ ${event.class} · ${event.operation}\n   ${event.target} (${event.actor}/${event.pid})`;
      break;
    case 'grant':
      item.textContent = `✔ grant ${event.root}\n   ${event.reason}`;
      break;
    case 'retry':
      item.textContent = `↻ retry #${event.attempt} with [${event.grants.join(', ')}]`;
      break;
    case 'session.closed':
      item.textContent =
        `■ closed exit=${event.exit_code} · violations=${event.violations} · ` +
        `grants=${event.grants} · retried=${event.retried} · ${event.duration_ms}ms`;
      break;
    case 'warning':
    case 'error':
      item.textContent = `⚠ ${event.code}: ${event.message}`;
      break;
    default:
      item.textContent = JSON.stringify(event);
  }
  els.events.prepend(item);
  while (els.events.children.length > 30) els.events.lastChild.remove();
}

function renderPolicy(event) {
  const policy = event.policy || {};
  const rows = [
    ['policy', event.policy_id],
    ['write', `${policy.write_default} · ${(policy.write_roots || []).join(', ')}`],
    ['delete', `${policy.delete_default} · ${(policy.delete_roots || []).join(', ')}`],
    ['network', policy.network_mode],
    ['auto-grant', (policy.auto_grant_roots || []).join(', ') || '—'],
  ];
  els.policy.textContent = '';
  for (const [key, value] of rows) {
    const dt = document.createElement('dt');
    dt.textContent = key;
    const dd = document.createElement('dd');
    dd.textContent = value;
    els.policy.append(dt, dd);
  }
}

async function runScenario(scenario) {
  return execute(scenario, scenario.command);
}

async function execute(scenario, command) {
  setRunning(true, 'running');
  clearTerminal();
  els.events.textContent = '';
  appendMeta(`$ ${command}\n`);
  appendMeta(`  sandbox-cli → sandbox-exec -f <profile> /bin/zsh -c …\n`);

  try {
    const record = await window.sbx.run({ command });
    if (record.stdout) appendLine('stdout', record.stdout);
    if (record.stderr) appendLine('stderr', record.stderr);
    const counts = countEvents(record.events || []);
    const problems = scenario ? evaluate(scenario, record) : [];
    const ok = problems.length === 0;
    appendMeta(
      `  ${ok ? '✔' : '✘'} exit=${record.exitCode} events=${JSON.stringify(counts)}\n`,
      ok ? 'ok' : 'meta',
    );
    if (scenario) {
      state.results.set(scenario.id, {
        id: scenario.id,
        title: scenario.title,
        command,
        exitCode: record.exitCode,
        counts,
        ok,
        detail: problems.join('; '),
        events: record.events,
        stdout: record.stdout,
      });
      renderScenarioList();
    }
    return record;
  } catch (error) {
    appendLine('stderr', `${error.message}\n`);
    if (scenario) {
      state.results.set(scenario.id, {
        id: scenario.id,
        title: scenario.title,
        command,
        exitCode: -1,
        counts: {},
        ok: false,
        detail: error.message,
      });
      renderScenarioList();
    }
    return { exitCode: -1, events: [], stdout: '', stderr: error.message };
  } finally {
    setRunning(false, 'idle');
  }
}

async function refreshStatus() {
  try {
    const status = await window.sbx.status();
    if (status) {
      els.centerPill.textContent = `center: running · pid ${status.pid}`;
      els.centerPill.className = 'pill pill-ok';
      els.policyPill.textContent = `policy ${status.policy_id}`;
      els.rulesPill.textContent = `rules ${status.rules} · sessions ${status.sessions} · up ${Math.round(status.uptime_ms / 1000)}s`;
    } else {
      els.centerPill.textContent = 'center: stopped';
      els.centerPill.className = 'pill pill-idle';
    }
  } catch (error) {
    els.centerPill.textContent = `center: ${error.message}`;
    els.centerPill.className = 'pill pill-idle';
  }
}

async function refreshAudit() {
  try {
    const result = await window.sbx.events(18);
    els.auditBody.textContent = '';
    for (const event of (result.events || []).slice().reverse()) {
      const row = document.createElement('tr');
      const cells = [
        ['', String(event.seq)],
        ['', event.ts.replace('T', ' ').replace('Z', '')],
        [`kind-${event.kind.replace('.', '_')}`, event.kind],
        ['', event.session_id || '—'],
        ['data', summarize(event.data)],
      ];
      for (const [className, text] of cells) {
        const cell = document.createElement('td');
        if (className) cell.className = className;
        cell.textContent = text;
        row.appendChild(cell);
      }
      els.auditBody.appendChild(row);
    }
  } catch {
    // The audit pane is best-effort.
  }
}

function summarize(data) {
  if (!data || typeof data !== 'object') return String(data);
  const interesting = ['target', 'operation', 'root', 'reason', 'exit_code', 'violations', 'grants', 'retried', 'policy_id', 'tag', 'command', 'pid'];
  const parts = [];
  for (const key of interesting) {
    if (data[key] !== undefined) {
      parts.push(`${key}=${typeof data[key] === 'string' ? data[key] : JSON.stringify(data[key])}`);
    }
  }
  return parts.join(' ') || JSON.stringify(data);
}

async function boot() {
  state.paths = await window.sbx.paths();
  state.scenarios = buildScenarios(state.paths);
  appendMeta(`sandbox-center MVP · ${state.paths.versions.electron ? `electron ${state.paths.versions.electron} · node ${state.paths.versions.node}` : ''}\n`);
  appendMeta(`workspace ${state.paths.workspace}\n`);
  appendMeta(`policy    ${state.paths.policy}\n`);
  appendMeta(`socket    ${state.paths.socket}\n\n`);
  renderScenarioList();
  await refreshStatus();
  await refreshAudit();

  window.sbx.onOutput((message) => appendLine(message.stream, message.chunk));
  window.sbx.onEvent((message) => pushEvent(message.event));
  window.sbx.onStatusChanged(() => refreshStatus());
  window.sbx.onCenterLog((line) => appendMeta(`[center] ${line}\n`));

  els.form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const command = els.input.value.trim();
    if (!command || state.running) return;
    els.input.value = '';
    await execute(null, command);
  });
  els.cancelButton.addEventListener('click', () => window.sbx.cancel());

  setInterval(refreshStatus, 3000);
  setInterval(refreshAudit, 2000);
}

// --- self-test entry point (used by `npm run verify`) ----------------------
window.__selftest = {
  async runAll() {
    const results = [];
    for (const scenario of state.scenarios) {
      await execute(scenario, scenario.command);
      const result = state.results.get(scenario.id);
      results.push(result);
    }
    const ok = results.every((result) => result.ok);
    return {
      ok,
      scenarios: results.map(({ events, stdout, ...rest }) => rest),
    };
  },
};

boot().catch((error) => {
  appendMeta(`boot failed: ${error.message}\n`);
});
