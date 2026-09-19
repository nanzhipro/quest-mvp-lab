// The demo / self-test matrix. Every scenario is a real tool call executed by
// `sandbox-cli` under a real Seatbelt profile; `expect` describes what the
// sandbox must have done.

export function buildScenarios(paths) {
  const appHome = paths.appHome;

  return [
    {
      id: 'write-inside-workspace',
      title: '工作区内写文件（应放行）',
      command: "printf 'hello from inside the sandbox\\n' > hello.txt && cat hello.txt",
      expect: { zero: true, noViolations: true },
    },
    {
      id: 'write-outside-workspace',
      title: '工作区外写文件（应拒绝）',
      command: 'echo pwned > ~/.sandbox-center-mvp-escape.txt',
      expect: { zero: false, violations: ['write'] },
    },
    {
      id: 'delete-inside-workspace',
      title: '删除工作区文件（应拒绝）',
      command: "printf 'precious\\n' > precious.txt && rm precious.txt",
      expect: { zero: false, violations: ['delete'] },
    },
    {
      id: 'delete-in-trash',
      title: '删除 .sc-trash 内文件（应放行）',
      command: "printf 'bye\\n' > .sc-trash/tmp.txt && rm .sc-trash/tmp.txt && echo trash-ok",
      expect: { zero: true, noViolations: true, stdoutContains: ['trash-ok'] },
    },
    {
      id: 'auto-grant-cache',
      title: '删除缓存（拒绝 → 授权 → 重试成功）',
      command:
        `printf 'blob' > '${appHome}/cache-demo/blob.bin' && ` +
        `rm '${appHome}/cache-demo/blob.bin' && echo cache-regenerated`,
      expect: { zero: true, violations: ['delete'], grant: true, retry: true, stdoutContains: ['cache-regenerated'] },
    },
    {
      id: 'network-public',
      title: '访问公网（应拒绝）',
      command: '/usr/bin/nc -w 2 1.1.1.1 80 </dev/null',
      expect: { zero: false, violations: ['network'] },
    },
    {
      id: 'network-loopback',
      title: '访问 localhost（应放行）',
      command:
        'python3 -c "import socket; s=socket.socket(); s.bind((\'127.0.0.1\',0)); s.listen(1); ' +
        "p=s.getsockname()[1]; c=socket.socket(); c.connect(('127.0.0.1',p)); print('loopback-ok')\"",
      expect: { zero: true, noViolations: true, stdoutContains: ['loopback-ok'] },
    },
    {
      id: 'interpreters',
      title: 'python3 / node / file 在沙箱内运行',
      command:
        "printf hello > probe.txt; python3 -c 'print(6*7)'; node -e 'console.log(6*7)'; file probe.txt",
      expect: { zero: true, noViolations: true, stdoutContains: ['42', '42', 'ASCII text'] },
    },
  ];
}

export function countEvents(events) {
  return events.reduce((counts, event) => {
    const key = event.type === 'violation' ? `violation:${event.class}` : event.type;
    counts[key] = (counts[key] || 0) + 1;
    return counts;
  }, {});
}

/** Returns a list of human-readable problems; empty means the scenario passed. */
export function evaluate(scenario, record) {
  const events = record.events || [];
  const problems = [];
  const violations = events.filter((event) => event.type === 'violation');

  if (scenario.expect.zero && record.exitCode !== 0) {
    problems.push(`exit=${record.exitCode} (expected 0)`);
  }
  if (scenario.expect.zero === false && record.exitCode === 0) {
    problems.push('exit=0 (expected a denial)');
  }
  if (scenario.expect.noViolations && violations.length > 0) {
    problems.push(`unexpected violation ${violations[0].class}`);
  }
  for (const klass of scenario.expect.violations || []) {
    if (!violations.some((event) => event.class === klass)) {
      problems.push(`missing ${klass} violation`);
    }
  }
  if (scenario.expect.grant && !events.some((event) => event.type === 'grant')) {
    problems.push('missing grant event');
  }
  if (scenario.expect.retry && !events.some((event) => event.type === 'retry')) {
    problems.push('missing retry event');
  }
  for (const needle of scenario.expect.stdoutContains || []) {
    if (!(record.stdout || '').includes(needle)) {
      problems.push(`stdout missing "${needle}"`);
    }
  }
  return problems;
}
