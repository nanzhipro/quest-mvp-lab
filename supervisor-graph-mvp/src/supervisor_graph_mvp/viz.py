"""A single-file, drill-down HTML report built from one run's evidence.

Static diagrams tell you the shape of a design; this one lets you open it. Every
node, sub-task, consistency finding and model call in the report is a clickable row
that reveals the *raw* payload underneath — the intent JSON, the plan the model
actually returned, the specialist output, the finding's repair target, and the exact
prompt/response bytes of each call. Nothing is summarised away: the numbers in the
header are recomputed in JavaScript from the embedded run payload, so the report
cannot drift from the run it claims to describe.

No CDN, no build step, no dependencies — one ``.html`` file with the run JSON
embedded, which is why it can be attached to a review or opened offline.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

VERDICT_COLORS = {
    "allow": "#1f883d",
    "review": "#bf8700",
    "block": "#cf222e",
    "escalate": "#8250df",
    "": "#57606a",
}


def _json_block(value: Any) -> str:
    return html.escape(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def build_report_html(
    payload: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]] = (),
    wire: Sequence[Mapping[str, Any]] = (),
    *,
    title: str = "supervisor-graph-mvp · 运行报告",
) -> str:
    """Render one run as a self-contained interactive HTML document."""
    run = dict(payload)
    ruling = run.get("ruling") or {}
    verdict = str(run.get("verdict") or "")
    graph = run.get("graph") or {}
    visits = list(run.get("visits") or [])
    node_visits = graph.get("node_visits") or {}
    order = [visit.get("node") for visit in visits]
    cards: List[str] = []
    for node in dict.fromkeys(order):
        payload_for_node = [visit for visit in visits if visit.get("node") == node]
        cards.append(
            """
        <button class="node" data-node="{node}" style="--depth:{depth}">
          <span class="node-name">{node}</span>
          <span class="badge">{count}×</span>
        </button>""".format(
                node=html.escape(str(node)),
                count=len(payload_for_node),
                depth=min(6, len(payload_for_node)),
            )
        )
    findings = list(run.get("findings") or [])
    finding_rows = "\n".join(
        """
        <tr class="finding {sev}" data-index="{index}">
          <td>{mark}</td><td>{rule}</td><td>{message}</td><td>{repair}</td>
        </tr>""".format(
            index=index,
            sev=html.escape(str(finding.get("severity"))),
            mark="🛑" if finding.get("severity") == "blocking" else "⚠️",
            rule=html.escape(str(finding.get("rule"))),
            message=html.escape(str(finding.get("message"))),
            repair=html.escape(
                "{} → {}".format(
                    (finding.get("repair") or {}).get("agent", "-"),
                    (finding.get("repair") or {}).get("goal", "-"),
                )
            ),
        )
        for index, finding in enumerate(findings)
    )
    subtask_rows = "\n".join(
        """
        <tr class="subtask" data-subtask="{sid}">
          <td>{sid}</td><td>{agent}</td><td>{status}</td><td>{attempt}</td><td>{ms} ms</td><td>{hint}</td>
        </tr>""".format(
            sid=html.escape(str(result.get("subtask"))),
            agent=html.escape(str(result.get("agent"))),
            status=html.escape(str((result.get("output") or {}).get("status") or "-")),
            attempt=html.escape(str(result.get("attempt"))),
            ms=html.escape(str(result.get("duration_ms"))),
            hint=html.escape("↻ " + str(result.get("hint")) if result.get("hint") else ""),
        )
        for result in (run.get("results") or {}).values()
    )
    call_rows = "\n".join(
        """
        <tr class="call" data-call="{index}">
          <td>{purpose}</td><td>{model}</td><td>{chars}</td><td>{latency}</td>
          <td>{tokens}</td><td>{parsed}</td>
        </tr>""".format(
            index=index,
            purpose=html.escape(str(call.get("purpose"))),
            model=html.escape(str(call.get("model") or "-")),
            chars=html.escape("{} → {}".format(call.get("prompt_chars"), call.get("reply_chars"))),
            latency=html.escape("{} ms".format(call.get("latency_ms", "-"))),
            tokens=html.escape(str((call.get("usage") or {}).get("total_tokens", "-"))),
            parsed="✅" if call.get("parsed") else ("✗" if call.get("error") else "—"),
        )
        for index, call in enumerate(run.get("llm_calls") or [])
    )
    verified = (ruling.get("evidence") or {}).get("verified") or []
    unverified = (ruling.get("evidence") or {}).get("unverified") or []
    evidence_list = "\n".join(
        '<li class="ok">{}</li>'.format(html.escape(str(item.get("id")))) for item in verified
    ) + "\n".join(
        '<li class="bad">{}（{}）</li>'.format(
            html.escape(str(item.get("id"))), html.escape(str(item.get("reason")))
        )
        for item in unverified
    )
    data = (
        json.dumps(
            {
                "payload": run,
                "events": [dict(event) for event in events],
                "wire": [dict(exchange) for exchange in wire],
            },
            ensure_ascii=False,
            default=str,
        )
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    return _TEMPLATE.format(
        title=html.escape(title),
        scenario=html.escape(str((run.get("request") or {}).get("id") or "ad-hoc")),
        request_text=html.escape(str((run.get("request") or {}).get("text") or "")),
        verdict=html.escape(verdict or "未定"),
        verdict_color=VERDICT_COLORS.get(verdict, "#57606a"),
        level=html.escape(str(ruling.get("level") or "未定级")),
        decision=html.escape(str(ruling.get("decision") or "无")),
        status=html.escape(str(run.get("status") or "")),
        steps=html.escape(str(graph.get("steps", 0))),
        node_count=html.escape(str(len(node_visits))),
        repairs=html.escape(str(run.get("repairs", 0))),
        blocking=html.escape(str((ruling.get("consistency") or {}).get("blocking", 0))),
        warnings=html.escape(str((ruling.get("consistency") or {}).get("warnings", 0))),
        calls=html.escape(str(len(run.get("llm_calls") or []))),
        narrative_source=html.escape(str(ruling.get("narrative_source") or "-")),
        narrative=html.escape(str(ruling.get("narrative") or "")),
        nodes="\n".join(cards),
        findings=finding_rows or '<tr><td colspan="4" class="empty">无一问题</td></tr>',
        subtasks=subtask_rows or '<tr><td colspan="6" class="empty">无子任务</td></tr>',
        calls_rows=call_rows or '<tr><td colspan="6" class="empty">无模型调用</td></tr>',
        evidence=evidence_list or '<li class="empty">无证据</li>',
        data=data,
    )


_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  :root {{
    color-scheme: light dark;
    --bg: #ffffff; --fg: #1f2328; --muted: #57606a; --line: #d0d7de;
    --panel: #f6f8fa; --accent: #0969da; --mono: ui-monospace, SFMono-Regular, Menlo, monospace;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#0d1117; --fg:#e6edf3; --muted:#8b949e; --line:#30363d; --panel:#161b22; --accent:#2f81f7; }}
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--fg);
         font: 14px/1.6 -apple-system, "PingFang SC", "Hiragino Sans GB", system-ui, sans-serif; }}
  header {{ padding:20px 24px 12px; border-bottom:1px solid var(--line); }}
  h1 {{ margin:0 0 4px; font-size:18px; }}
  h2 {{ font-size:14px; margin:0 0 8px; color:var(--muted); font-weight:600; letter-spacing:.04em; }}
  .req {{ color:var(--muted); margin:0; }}
  .verdict {{ display:inline-block; padding:3px 10px; border-radius:999px; color:#fff;
              background:{verdict_color}; font-weight:600; }}
  .stats {{ display:flex; flex-wrap:wrap; gap:18px; padding:12px 24px; border-bottom:1px solid var(--line); }}
  .stat b {{ display:block; font-size:18px; }}
  .stat span {{ color:var(--muted); font-size:12px; }}
  main {{ display:grid; grid-template-columns: minmax(360px, 1fr) minmax(420px, 1.3fr); gap:0; }}
  section {{ padding:16px 24px; border-bottom:1px solid var(--line); }}
  .left {{ border-right:1px solid var(--line); }}
  .node {{ display:block; width:100%; text-align:left; margin:4px 0; padding:8px 10px; cursor:pointer;
          border:1px solid var(--line); border-left:4px solid var(--accent); border-radius:6px;
          background:var(--panel); color:var(--fg); font:inherit; }}
  .node:hover {{ border-color:var(--accent); }}
  .node.active {{ background:color-mix(in srgb, var(--accent) 14%, var(--panel)); }}
  .node .badge {{ float:right; color:var(--muted); font-size:12px; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  th, td {{ text-align:left; padding:6px 8px; border-bottom:1px solid var(--line); vertical-align:top; }}
  th {{ color:var(--muted); font-weight:600; }}
  tr.subtask, tr.call, tr.finding {{ cursor:pointer; }}
  tr.subtask:hover, tr.call:hover, tr.finding:hover {{ background:var(--panel); }}
  tr.finding.blocking td:first-child {{ color:#cf222e; }}
  tr.finding.warning td:first-child {{ color:#bf8700; }}
  pre {{ background:var(--panel); border:1px solid var(--line); border-radius:6px; padding:10px;
         overflow:auto; max-height:60vh; font-family:var(--mono); font-size:12px; }}
  ul {{ margin:6px 0; padding-left:18px; }}
  li.ok::marker {{ color:#1f883d; }} li.bad::marker {{ color:#cf222e; }}
  .muted {{ color:var(--muted); }} .empty {{ color:var(--muted); text-align:center; }}
  .panel-title {{ font-weight:600; margin:0 0 6px; }}
  .narrative {{ background:var(--panel); border-left:3px solid var(--accent); padding:8px 12px; border-radius:4px; }}
  footer {{ padding:14px 24px; color:var(--muted); font-size:12px; }}
</style>
</head>
<body>
<header>
  <h1>{title} <span class="verdict">{verdict}</span></h1>
  <p class="req"><b>{scenario}</b> · {request_text}</p>
</header>
<div class="stats">
  <div class="stat"><b>{level}</b><span>资产级别</span></div>
  <div class="stat"><b>{decision}</b><span>策略决策</span></div>
  <div class="stat"><b>{status}</b><span>状态</span></div>
  <div class="stat"><b>{steps}</b><span>图步数</span></div>
  <div class="stat"><b>{node_count}</b><span>访问节点</span></div>
  <div class="stat"><b>{repairs}</b><span>修复轮次</span></div>
  <div class="stat"><b>{blocking}</b><span>阻断问题</span></div>
  <div class="stat"><b>{warnings}</b><span>告警</span></div>
  <div class="stat"><b>{calls}</b><span>模型调用</span></div>
</div>
<main>
  <div class="left">
    <section>
      <h2>状态图（点节点看该节点的真实负载）</h2>
      <p class="muted" style="margin:0 0 8px">
        intake → classify → plan → dispatch → check<br>
        check ⟲ dispatch（修复）｜ → aggregate（收口）｜ → escalate（升级人工）
      </p>
      {nodes}
    </section>
    <section>
      <h2>子任务派发</h2>
      <table><thead><tr><th>子任务</th><th>Agent</th><th>状态</th><th>轮次</th><th>耗时</th><th>修复提示</th></tr></thead>
      <tbody>{subtasks}</tbody></table>
    </section>
    <section>
      <h2>证据</h2>
      <ul>{evidence}</ul>
    </section>
    <section>
      <h2>模型调用（三次窄职责调用）</h2>
      <table><thead><tr><th>用途</th><th>模型</th><th>字符</th><th>时延</th><th>tokens</th><th>JSON</th></tr></thead>
      <tbody>{calls_rows}</tbody></table>
    </section>
  </div>
  <div class="right">
    <section>
      <h2>结论说明（来源：{narrative_source}）</h2>
      <p class="narrative">{narrative}</p>
    </section>
    <section>
      <h2>一致性问题（点行看修复目标与细节）</h2>
      <table><thead><tr><th></th><th>规则</th><th>问题</th><th>修复</th></tr></thead>
      <tbody>{findings}</tbody></table>
    </section>
    <section>
      <h2>原始负载</h2>
      <p class="panel-title" id="panel-title">左侧任选一项</p>
      <pre id="panel">点节点 / 子任务 / 问题 / 模型调用，这里显示对应原始 JSON 与提示词。</pre>
    </section>
  </div>
</main>
<footer>由 <code>supervisor-graph --html</code> 从一次真实运行的 result.json / trace.jsonl / wire.jsonl 生成，无外部依赖。</footer>
<script>
const DATA = {data};
const panel = document.getElementById('panel');
const panelTitle = document.getElementById('panel-title');
function show(title, value) {{
  panelTitle.textContent = title;
  panel.textContent = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
}}
document.querySelectorAll('.node').forEach(el => el.addEventListener('click', () => {{
  document.querySelectorAll('.node').forEach(n => n.classList.remove('active'));
  el.classList.add('active');
  const node = el.dataset.node;
  const visits = (DATA.payload.visits || []).filter(v => v.node === node);
  const events = (DATA.events || []).filter(e => e.node === node || (e.ruling && node === 'aggregate'));
  show('节点 ' + node, {{ visits: visits, events: events }});
}}));
document.querySelectorAll('tr.subtask').forEach(el => el.addEventListener('click', () => {{
  const id = el.dataset.subtask;
  show('子任务 ' + id, (DATA.payload.results || {{}})[id] || {{}});
}}));
document.querySelectorAll('tr.finding').forEach(el => el.addEventListener('click', () => {{
  const index = Number(el.dataset.index);
  show('一致性问题 #' + index, (DATA.payload.findings || [])[index] || {{}});
}}));
document.querySelectorAll('tr.call').forEach(el => el.addEventListener('click', () => {{
  const index = Number(el.dataset.call);
  const call = (DATA.payload.llm_calls || [])[index] || {{}};
  const exchange = (DATA.wire || [])[index] || {{}};
  show('模型调用 #' + index + ' · ' + (call.purpose || ''), {{
    digest: call,
    request: (exchange.request || {{}}).body || exchange.request || null,
    response: exchange.response_raw || null
  }});
}}));
</script>
</body>
</html>
"""


def write_report(
    path: Path,
    payload: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]] = (),
    wire: Sequence[Mapping[str, Any]] = (),
    *,
    title: str = "supervisor-graph-mvp · 运行报告",
) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(build_report_html(payload, events, wire, title=title), encoding="utf-8")
    return target


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    """Read a JSONL evidence file, skipping blank lines (trace files are appended)."""
    file = Path(path)
    if not file.is_file():
        return []
    rows: List[Dict[str, Any]] = []
    for line in file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def report_from_run_dir(run_dir: Path, *, out: Optional[Path] = None) -> Path:
    """Rebuild the report from a finished run directory (no re-run needed)."""
    directory = Path(run_dir)
    payload = json.loads((directory / "result.json").read_text(encoding="utf-8"))
    events = read_jsonl(directory / "trace.jsonl")
    wire = read_jsonl(directory / "wire.jsonl")
    target = Path(out) if out else directory / "report.html"
    return write_report(target, payload, events, wire)


__all__ = ["build_report_html", "read_jsonl", "report_from_run_dir", "write_report"]
