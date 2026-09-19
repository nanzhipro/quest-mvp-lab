#!/usr/bin/env python3
"""Build the interactive message-assembly explorer from a capture run.

Reads one conversation capture (default: the 2026-09-11 heartbeat run), takes the
first agent loop — five POST rounds, messages 2 → 5 → 7 → 9 → 11 — and emits a
single self-contained HTML file that:

  * draws the loop as a sequence diagram (left band = exchange, right band = the
    messages[] array per round, new rows marked),
  * lists every message of every round below it, each one clickable for its exact
    payload (role, content, tool_calls, tool_call_id), with carried-over messages
    linking back to the round where they entered the array,
  * steps through the five rounds with the diagram-design canonical motion
    controller (copied verbatim from the skill template).

Static-first: without JavaScript, in print, and under prefers-reduced-motion the
whole figure is visible and complete — the controller only adds stepping.

    python3 build_explorer.py [run-dir] [out.html]
"""

import html
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
SKILL = pathlib.Path.home() / ".agents/skills/diagram-design"
ROUNDS = [14, 16, 17, 18, 19]

CJK = "'Geist', 'PingFang SC', 'Noto Sans SC', 'Microsoft YaHei', sans-serif"
MONO = "'Geist Mono', monospace"

ROLE_LABEL = {"system": "SYSTEM", "user": "USER", "assistant": "ASSISTANT", "tool": "TOOL"}


def esc(s: str) -> str:
    return html.escape(s, quote=True)


def describe(m: dict) -> str:
    """One-line human description of a message for the summary row."""
    role = m.get("role")
    if role == "system":
        return f"系统提示词 {len(m.get('content') or ''):,} 字符 + 29 个工具 schema"
    if role == "user":
        return f"“{m.get('content')}”"
    if role == "tool":
        body = m.get("content") or ""
        try:
            inner = json.loads(body)
            if isinstance(inner, dict) and "output" in inner:
                return f"{m.get('name')} → 命令输出（{len(body):,} 字符）"
            if isinstance(inner, dict) and "total_count" in inner:
                return f"{m.get('name')} → 命中 {inner['total_count']} 项（{len(body):,} 字符）"
            if isinstance(inner, dict) and "content" in inner:
                return f"{m.get('name')} → 文件正文（{len(body):,} 字符）"
        except Exception:
            pass
        return f"{m.get('name')} → 工具返回（{len(body):,} 字符）"
    if role == "assistant":
        calls = m.get("tool_calls") or []
        if calls:
            names = "、".join((c.get("function") or {}).get("name", "?") for c in calls)
            return f"tool_calls ×{len(calls)}（{names}）"
        return f"最终回答（{len(m.get('content') or ''):,} 字符）"
    return role or "?"


def payload_blocks(m: dict) -> list[tuple[str, str]]:
    """(label, text) blocks shown when a message is expanded."""
    blocks = [("原始消息 JSON（发给模型的字段）", json.dumps(m, ensure_ascii=False, indent=2))]
    if m.get("role") == "tool":
        try:
            parsed = json.loads(m.get("content") or "")
            blocks.append(("解析后的工具返回（content 字段是 JSON 字符串）",
                           json.dumps(parsed, ensure_ascii=False, indent=2)))
        except Exception:
            pass
    for tc in m.get("tool_calls") or []:
        fn = tc.get("function") or {}
        try:
            args = json.dumps(json.loads(fn.get("arguments") or "{}"), ensure_ascii=False, indent=2)
        except Exception:
            args = fn.get("arguments") or ""
        blocks.append((f"工具调用参数 · {fn.get('name')}", args))
    return blocks


def build(run_dir: pathlib.Path, out: pathlib.Path) -> None:
    conv = json.loads((run_dir / "conversation.json").read_text(encoding="utf-8"))
    by_ex = {c["exchange"]: c for c in conv}
    events = [json.loads(l) for l in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    # direction matters: request and response share a seq — request carries body_bytes,
    # response carries latency_ms. Merging them blindly silently swaps the two.
    reqs = {e["seq"]: e for e in events if e["direction"] == "request"}
    resps = {e["seq"]: e for e in events if e["direction"] == "response"}

    # ---- collect the rounds and the unique messages -------------------------
    rounds, seen, order = [], {}, []
    for n, seq in enumerate(ROUNDS, start=1):
        c = by_ex[seq]
        msgs = c["messages"]
        resp = c["response"] or {}
        new_ids = []
        for m in msgs:
            key = json.dumps(m, ensure_ascii=False, sort_keys=True)
            if key not in seen:
                seen[key] = f"msg-{len(order) + 1:02d}"
                order.append({"id": seen[key], "msg": m, "round": n})
                new_ids.append(seen[key])
        rounds.append({
            "n": n, "seq": seq, "msgs": msgs,
            "new_ids": new_ids,
            "carried": len(msgs) - len(new_ids),
            "bytes": reqs[seq]["body_bytes"], "latency": resps[seq]["latency_ms"],
            "usage": resp.get("usage") or {},
            "tool_calls": resp.get("tool_calls") or [],
            "content": resp.get("content") or "",
            "finish": resp.get("finish_reason"),
            "frames": resp.get("sse_frames"),
        })

    total_bytes = [r["bytes"] for r in rounds]
    total_tok = [r["usage"].get("prompt_tokens", 0) for r in rounds]

    # ---- SVG: sequence diagram, five rounds ---------------------------------
    POST_Y = {1: 164, 2: 292, 3: 420, 4: 548, 5: 676}
    RESP_Y = {n: POST_Y[n] + 28 for n in POST_Y}
    LOOP_TOP = {n: POST_Y[n] + 52 for n in (1, 2, 3, 4)}
    svg = []
    a = svg.append
    a('<svg viewBox="0 0 1280 1000" xmlns="http://www.w3.org/2000/svg" role="img" aria-labelledby="ma-title ma-desc">')
    a('<title id="ma-title">本地 Agent 与 LLM 的一次完整 Agent Loop：消息如何逐轮叠加</title>')
    a('<desc id="ma-desc">时序图展示一次完整的 Agent Loop：用户提出一个问题，本地 Agent 连续五次向无状态 LLM API 发送 POST 请求；每次请求体里的 messages 数组都比上一轮更长，系统提示词与用户消息固定在最前，模型返回的 tool_calls 与本地工具执行结果依次追加到数组末尾。右侧卡片显示每轮请求体的构成、字节数、消息条数与输入 token 数。可逐步回放五轮。</desc>')
    a('<defs>')
    for mid, col in (("arrow", "#4f5d75"), ("arrow-accent", "#eb6c36"), ("arrow-link", "#2e5aa8")):
        a(f'<marker id="{mid}" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto"><polygon points="0 0, 8 3, 0 6" fill="{col}"/></marker>')
    a('</defs>')
    a('<rect width="100%" height="100%" fill="#f5f5f5"/>')

    # band labels + divider
    a(f'<text x="40" y="48" fill="#2d3142" font-size="12" font-weight="500" font-family="{CJK}">① 消息交换 · 时间向下</text>')
    a(f'<text x="816" y="48" fill="#2d3142" font-size="12" font-weight="500" font-family="{CJK}">② 请求体 messages[]</text>')
    a(f'<text x="816" y="64" fill="#4f5d75" font-size="12" font-family="{CJK}">只增不减 · 每轮整段重发</text>')
    a('<line x1="800" y1="56" x2="800" y2="896" stroke="rgba(45,49,66,0.12)" stroke-width="1"/>')

    # lifelines + activation
    for x in (144, 440, 680):
        a(f'<line x1="{x}" y1="112" x2="{x}" y2="812" stroke="rgba(45,49,66,0.20)" stroke-width="1" stroke-dasharray="3,3"/>')
    a('<rect x="436" y="132" width="8" height="620" fill="rgba(45,49,66,0.06)" stroke="#4f5d75" stroke-width="0.8"/>')
    for n in POST_Y:
        a(f'<rect x="676" y="{POST_Y[n]}" width="8" height="28" fill="rgba(45,49,66,0.06)" stroke="#4f5d75" stroke-width="0.8"/>')

    # user message (round 1)
    a('<line x1="144" y1="132" x2="432" y2="132" stroke="#4f5d75" stroke-width="1" marker-end="url(#arrow)"/>')
    a('<rect x="232" y="112" width="112" height="16" rx="2" fill="#f5f5f5"/>')
    a(f'<text x="288" y="124" fill="#4f5d75" font-size="12" text-anchor="middle" font-family="{CJK}">这个目录是干什么的</text>')

    # rounds: arrows (persistent) + per-step card group
    card_groups = []
    for r in rounds:
        n = r["n"]
        y, ry = POST_Y[n], RESP_Y[n]
        last = n == len(rounds)
        stroke = "#eb6c36" if last else "#4f5d75"
        mk = "arrow-accent" if last else "arrow"
        a(f'<line x1="440" y1="{y}" x2="672" y2="{y}" stroke="#4f5d75" stroke-width="1" marker-end="url(#arrow)"/>')
        a(f'<rect x="524" y="{y - 20}" width="72" height="12" rx="2" fill="#f5f5f5"/>')
        a(f'<text x="560" y="{y - 11}" fill="#4f5d75" font-size="8" text-anchor="middle" letter-spacing="0.06em" font-family="{MONO}">POST #{n}</text>')
        label = "content" if last else f'tool_calls ×{len(r["tool_calls"])}'
        a(f'<line x1="680" y1="{ry}" x2="448" y2="{ry}" stroke="{stroke}" stroke-width="{1.2 if last else 1}" stroke-dasharray="5,4" marker-end="url(#{mk})"/>')
        a(f'<rect x="{532 if last else 500}" y="{ry - 20}" width="{56 if last else 120}" height="12" rx="2" fill="#f5f5f5"/>')
        a(f'<text x="560" y="{ry - 11}" fill="{stroke}" font-size="8" text-anchor="middle" letter-spacing="0.06em" font-family="{MONO}">{label}</text>')
        if not last:
            t = LOOP_TOP[n]
            a(f'<path d="M 440 {t} L 480 {t} L 480 {t + 24} L 448 {t + 24}" fill="none" stroke="#4f5d75" stroke-width="1" stroke-linejoin="round" marker-end="url(#arrow)"/>')
            a(f'<rect x="488" y="{t + 4}" width="96" height="16" rx="2" fill="#f5f5f5"/>')
            a(f'<text x="496" y="{t + 16}" fill="#4f5d75" font-size="12" font-family="{CJK}">本地执行工具</text>')

        # card (inside this round's motion group)
        card = []
        new_rows = [it for it in order if it["round"] == n]
        h = 8 + 16 + 16 + 4 + len(new_rows) * 24 + 8
        card.append(f'<rect x="816" y="{y}" width="424" height="{h}" rx="6" fill="rgba(45,49,66,0.02)" stroke="rgba(45,49,66,0.16)" stroke-width="1"/>')
        card.append(f'<text x="828" y="{y + 16}" fill="#4f5d75" font-size="8" letter-spacing="0.06em" font-family="{MONO}">'
                    f'POST #{n} · messages {len(r["msgs"])} 条（+{len(r["new_ids"])}）· {r["bytes"]:,} B · {total_tok[n - 1]:,} tok</text>')
        card.append(f'<rect x="828" y="{y + 24}" width="400" height="16" rx="4" fill="rgba(45,49,66,0.03)" stroke="rgba(45,49,66,0.16)" stroke-width="0.8"/>')
        carried_txt = "本轮首次发送" if r["carried"] == 0 else f'已发送的 {r["carried"]} 条（整段重发）'
        card.append(f'<text x="840" y="{y + 36}" fill="#7a8399" font-size="12" font-family="{CJK}">{carried_txt}</text>')
        cy = y + 44
        for item in new_rows:
            m = item["msg"]
            card.append(f'<rect x="828" y="{cy}" width="400" height="20" rx="4" fill="#ffffff" stroke="#2d3142" stroke-width="1.2"/>')
            card.append(f'<rect x="828" y="{cy}" width="4" height="20" fill="#2d3142"/>')
            card.append(f'<text x="840" y="{cy + 14}" fill="#2d3142" font-size="8" font-weight="500" letter-spacing="0.08em" font-family="{MONO}">{ROLE_LABEL.get(m.get("role"), "?")}</text>')
            card.append(f'<text x="904" y="{cy + 14}" fill="#2d3142" font-size="12" font-family="{CJK}">{esc(describe(m))[:64]}</text>')
            cy += 24
        card_groups.append((n, "\n      ".join(card)))

    # cards are emitted inside motion groups so they reveal round by round
    for n, card in card_groups:
        a(f'<g data-motion-item data-step="{n}" aria-label="第 {n} 轮：POST #{n} 与其请求体卡片">')
        a(f'      {card}')
        a('</g>')

    # final answer arrow back to the user
    a('<line x1="440" y1="732" x2="152" y2="732" stroke="#4f5d75" stroke-width="1" stroke-dasharray="5,4" marker-end="url(#arrow)"/>')
    a('<rect x="272" y="712" width="48" height="16" rx="2" fill="#f5f5f5"/>')
    a(f'<text x="296" y="724" fill="#4f5d75" font-size="12" text-anchor="middle" font-family="{CJK}">回答</text>')

    # actors (persistent)
    a(f'<rect x="64" y="64" width="160" height="48" rx="6" fill="rgba(79,93,117,0.10)" stroke="#7a8399" stroke-width="1"/>')
    a(f'<text x="144" y="88" fill="#2d3142" font-size="12" font-weight="600" text-anchor="middle" font-family="{CJK}">用户</text>')
    a(f'<text x="144" y="102" fill="#4f5d75" font-size="9" text-anchor="middle" font-family="{MONO}">HUMAN</text>')
    a('<rect x="360" y="64" width="160" height="48" rx="6" fill="rgba(235,108,54,0.08)" stroke="#eb6c36" stroke-width="1"/>')
    a(f'<text x="440" y="88" fill="#2d3142" font-size="12" font-weight="600" text-anchor="middle" font-family="{CJK}">本地 Agent</text>')
    a(f'<text x="440" y="102" fill="#4f5d75" font-size="9" text-anchor="middle" font-family="{MONO}">AGENT · STATEFUL</text>')
    a('<rect x="600" y="64" width="160" height="48" rx="6" fill="rgba(45,49,66,0.03)" stroke="rgba(45,49,66,0.30)" stroke-width="1"/>')
    a(f'<text x="680" y="88" fill="#2d3142" font-size="12" font-weight="600" text-anchor="middle" font-family="{CJK}">LLM API</text>')
    a(f'<text x="680" y="102" fill="#4f5d75" font-size="9" text-anchor="middle" font-family="{MONO}">STATELESS</text>')

    # takeaway + legend (persistent)
    a(f'<text x="64" y="836" fill="#7a8399" font-size="8" letter-spacing="0.14em" font-family="{MONO}">TAKEAWAY</text>')
    a(f'<text x="64" y="856" fill="#2d3142" font-size="12" font-family="{CJK}">会话历史只存在本地一份，模型每轮拿到的都是 Agent 当场拼好的整段数组</text>')
    a('<line x1="40" y1="892" x2="1240" y2="892" stroke="rgba(45,49,66,0.10)" stroke-width="0.8"/>')
    a('<rect x="40" y="908" width="24" height="16" rx="2" fill="#ffffff" stroke="#2d3142" stroke-width="1.2"/><rect x="40" y="908" width="4" height="16" fill="#2d3142"/>')
    a(f'<text x="76" y="920" fill="#4f5d75" font-size="12" font-family="{CJK}">本轮新增（追加到数组末尾）</text>')
    a('<rect x="376" y="908" width="24" height="16" rx="2" fill="rgba(45,49,66,0.03)" stroke="rgba(45,49,66,0.16)" stroke-width="0.8"/>')
    a(f'<text x="412" y="920" fill="#4f5d75" font-size="12" font-family="{CJK}">上一轮已发过（本轮整段重发）</text>')
    a('<line x1="712" y1="916" x2="744" y2="916" stroke="#4f5d75" stroke-width="1" marker-end="url(#arrow)"/>')
    a(f'<text x="756" y="920" fill="#4f5d75" font-size="12" font-family="{CJK}">请求 POST</text>')
    a('<line x1="888" y1="916" x2="920" y2="916" stroke="#4f5d75" stroke-width="1" stroke-dasharray="5,4" marker-end="url(#arrow)"/>')
    a(f'<text x="932" y="920" fill="#4f5d75" font-size="12" font-family="{CJK}">模型返回（SSE）</text>')
    a('<path d="M 1104 912 L 1128 912 L 1128 924 L 1112 924" fill="none" stroke="#4f5d75" stroke-width="1" marker-end="url(#arrow)"/>')
    a(f'<text x="1140" y="920" fill="#4f5d75" font-size="12" font-family="{CJK}">本地执行</text>')
    a('</svg>')

    # ---- transcript ---------------------------------------------------------
    sections = []
    for r in rounds:
        n = r["n"]
        rows = []
        for m in r["msgs"]:
            key = json.dumps(m, ensure_ascii=False, sort_keys=True)
            mid = seen[key]
            role = ROLE_LABEL.get(m.get("role"), "?")
            desc = describe(m)
            size = f'{len(json.dumps(m, ensure_ascii=False)):,} 字符'
            if mid in r["new_ids"]:
                blocks = payload_blocks(m)
                inner = "\n".join(
                    f'<p class="block-label">{esc(lbl)}</p><pre class="payload">{esc(txt)}</pre>'
                    for lbl, txt in blocks
                )
                rows.append(f'''<li class="msg is-new" id="{mid}">
          <details>
            <summary><span class="role">{role}</span><span class="desc">{esc(desc)}</span><span class="size">{size}</span><span class="badge">＋ 本轮新增</span></summary>
            <div class="payload-wrap">{inner}</div>
          </details>
        </li>''')
            else:
                rows.append(f'''<li class="msg is-carried">
          <a href="#{mid}"><span class="role">{role}</span><span class="desc">{esc(desc)}</span><span class="size">已发送 · 整段重发 ↗</span></a>
        </li>''')
        resp_bits = []
        if r["tool_calls"]:
            names = "、".join((c.get("function") or {}).get("name", "?") for c in r["tool_calls"])
            resp_bits.append(f'assistant.tool_calls ×{len(r["tool_calls"])}（{names}） · finish_reason={r["finish"]}')
        else:
            resp_bits.append(f'最终回答 {len(r["content"]):,} 字符 · finish_reason={r["finish"]}')
        u = r["usage"]
        sections.append(f'''<section class="round" data-motion-item data-step="{n}" id="round-{n}" aria-label="第 {n} 轮 POST #{n}：messages {len(r["msgs"])} 条">
      <header class="round-head">
        <span class="tag">POST #{n}</span>
        <span class="num">{len(r["msgs"])} 条消息</span>
        <span class="num">＋{len(r["new_ids"])} 新增</span>
        <span class="num">{r["bytes"]:,} B</span>
        <span class="num">{u.get("prompt_tokens", 0):,} tok（命中 {u.get("prompt_cache_hit_tokens", 0):,}）</span>
        <span class="num">{r["latency"]} ms · {r["frames"]} 帧</span>
      </header>
      <p class="resp">响应 → {esc("；".join(resp_bits))}</p>
      <ol class="msgs">
        {"".join(rows)}
      </ol>
    </section>''')
    sections_txt = "\n    ".join(sections)

    # ---- canonical motion controller, verbatim from the skill template ------
    tpl = (SKILL / "assets/template-motion.html").read_text(encoding="utf-8")
    m = re.search(r'<script data-diagram-controls>.*?</script>', tpl, re.S)
    if not m:
        raise SystemExit("canonical controller not found in template-motion.html")
    controller = m.group(0)

    doc = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>消息是怎么叠上去的 — 逐轮回放</title>
  <link href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=Geist:wght@400;500;600&family=Geist+Mono:wght@400;500;600&family=Noto+Sans+SC:wght@400;500;600&family=Noto+Sans+KR:wght@400;500;600&family=Noto+Serif+KR:wght@400&family=Noto+Sans+TC:wght@400;500;600&family=Noto+Serif+TC:wght@400&display=swap" rel="stylesheet">
  <style>
    *, *::before, *::after {{ box-sizing: border-box; }}
    :root {{
      --color-paper: #f5f5f5;
      --color-ink: #2d3142;
      --color-muted: #4f5d75;
      --color-soft: #7a8399;
      --color-accent: #eb6c36;
      --color-rule: rgba(45,49,66,0.12);
      --font-sans: 'Geist', 'PingFang SC', 'Noto Sans SC', 'Microsoft YaHei', system-ui, sans-serif;
      --font-serif: 'Instrument Serif', 'Noto Serif SC', serif;
      --font-mono: 'Geist Mono', ui-monospace, monospace;
      --motion-fast: 160ms;
      --motion-step: 480ms;
      --motion-hold: 720ms;
      --motion-total: 3600ms;
      --motion-ease: cubic-bezier(.2,.8,.2,1);
    }}
    body {{
      margin: 0; padding: 48px 32px 64px;
      color: var(--color-ink); background: var(--color-paper);
      font-family: var(--font-sans); line-height: 1.6;
    }}
    [data-motion-root] {{ width: min(100%, 1280px); margin: 0 auto; }}
    .eyebrow {{ margin: 0 0 8px; color: var(--color-muted); font: 500 11px/1.4 var(--font-mono); letter-spacing: .18em; text-transform: uppercase; }}
    h1 {{ margin: 0 0 12px; font: 400 clamp(24px,2.4vw,32px)/1.15 var(--font-serif); }}
    .lede {{ margin: 0 0 8px; max-width: 74ch; color: var(--color-muted); font-size: 15px; }}
    .lede strong {{ color: var(--color-ink); font-weight: 600; }}
    .hint {{ margin: 0 0 24px; color: var(--color-soft); font: 500 12px/1.5 var(--font-mono); }}
    svg {{ display: block; width: 100%; min-width: 900px; }}

    .rounds {{ margin: 32px 0 0; border-top: 1px solid var(--color-rule); padding-top: 24px; }}
    .round {{ margin: 0 0 24px; }}
    .round-head {{ display: flex; flex-wrap: wrap; gap: 12px; align-items: baseline; margin-bottom: 6px; }}
    .tag {{ font: 600 12px/1 var(--font-mono); letter-spacing: .1em; color: var(--color-ink);
            border: 1px solid var(--color-ink); border-radius: 4px; padding: 6px 8px; }}
    .num {{ font: 500 12px/1.2 var(--font-mono); color: var(--color-muted); }}
    .resp {{ margin: 0 0 8px; font: 500 12px/1.5 var(--font-mono); color: var(--color-soft); }}
    .msgs {{ list-style: none; margin: 0; padding: 0; border-left: 2px solid var(--color-rule); }}
    .msg {{ margin: 0 0 4px; }}
    .msg > details > summary, .msg > a {{
      display: grid; grid-template-columns: 96px minmax(0,1fr) auto auto;
      gap: 12px; align-items: baseline; padding: 8px 12px; cursor: pointer;
      border-radius: 4px; text-decoration: none; color: inherit;
    }}
    .msg.is-new > details {{ background: #fff; border: 1.2px solid var(--color-ink); border-radius: 4px; }}
    .msg.is-carried > a {{ background: rgba(45,49,66,0.03); border: 0.8px solid var(--color-rule); }}
    .msg > details > summary:focus-visible, .msg > a:focus-visible {{ outline: 3px solid var(--color-accent); outline-offset: 2px; }}
    .role {{ font: 500 10px/1.4 var(--font-mono); letter-spacing: .08em; color: var(--color-ink); }}
    .msg.is-carried .role {{ color: var(--color-muted); }}
    .desc {{ font-size: 13px; min-width: 0; }}
    .msg.is-carried .desc {{ color: var(--color-soft); }}
    .size {{ font: 400 11px/1.4 var(--font-mono); color: var(--color-soft); white-space: nowrap; }}
    .badge {{ font: 500 10px/1.4 var(--font-mono); color: var(--color-accent); letter-spacing: .06em; white-space: nowrap; }}
    .payload-wrap {{ padding: 4px 12px 12px 12px; border-top: 1px solid var(--color-rule); margin-top: 6px; }}
    .block-label {{ margin: 12px 0 4px; font: 500 10px/1.4 var(--font-mono); letter-spacing: .08em; text-transform: uppercase; color: var(--color-soft); }}
    pre.payload {{
      margin: 0; padding: 12px; max-height: 340px; overflow: auto;
      background: rgba(45,49,66,0.03); border: 0.8px solid var(--color-rule); border-radius: 4px;
      font: 400 11px/1.55 var(--font-mono); color: var(--color-ink);
      white-space: pre-wrap; word-break: break-word;
    }}
    footer {{ margin-top: 32px; padding-top: 12px; border-top: 1px solid var(--color-rule);
              font: 400 11px/1.6 var(--font-mono); color: var(--color-soft); }}

    .motion-ready [data-motion-item] {{ opacity: .30; transform: translateY(8px);
      transition: opacity var(--motion-step) var(--motion-ease), transform var(--motion-step) var(--motion-ease); }}
    .motion-ready [data-motion-item].is-visible,
    .motion-ready[data-frame="end"] [data-motion-item],
    .motion-ready[data-frame="static"] [data-motion-item] {{ opacity: 1; transform: none; }}
    .motion-ready [data-motion-item].is-current {{ outline: 2px solid var(--color-accent); outline-offset: 4px; }}
    [data-motion-controls] {{ display: none; align-items: center; flex-wrap: wrap; gap: 8px; position: sticky; bottom: 0;
      margin-top: 16px; padding: 16px 0; border-top: 1px solid var(--color-rule); background: var(--color-paper); }}
    .motion-ready [data-motion-controls] {{ display: flex; }}
    [data-motion-controls][hidden] {{ display: none !important; }}
    [data-motion-controls] button {{ min-width: 44px; min-height: 44px; padding: 8px 12px; border: 1px solid var(--color-muted);
      border-radius: 4px; color: var(--color-ink); background: var(--color-paper); font: 600 12px/1 var(--font-sans); cursor: pointer; }}
    [data-motion-controls] button:hover {{ border-color: var(--color-accent); }}
    [data-motion-controls] button:focus-visible {{ outline: 3px solid var(--color-accent); outline-offset: 2px; }}
    [data-motion-controls] button:disabled {{ cursor: not-allowed; opacity: .45; }}
    [data-motion-status-visible] {{ margin-left: auto; color: var(--color-muted); font: 500 12px/1.4 var(--font-mono); }}
    .sr-only {{ position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; border: 0; }}
    noscript p {{ margin: 12px 0 0; color: var(--color-muted); font: 500 12px/1.5 var(--font-mono); }}
    html[data-motion="static"] [data-motion-controls], html[data-motion="static"] [data-motion-decorative] {{ display: none !important; }}
    html[data-motion="static"] [data-motion-item] {{ opacity: 1 !important; transform: none !important; animation: none !important; transition: none !important; }}
    html[data-motion="step"] [data-motion-controls], html[data-motion="step"] [data-motion-decorative] {{ display: none !important; }}
    html[data-motion="step"] [data-motion-item] {{ animation: none !important; transition: none !important; }}
    @media (prefers-reduced-motion: reduce) {{
      *, *::before, *::after {{ animation-duration: .001ms !important; animation-iteration-count: 1 !important;
        scroll-behavior: auto !important; transition-duration: .001ms !important; }}
      [data-motion-item] {{ opacity: 1 !important; transform: none !important; }}
      [data-motion-decorative] {{ display: none !important; }}
      [data-motion-controls] {{ display: none !important; }}
    }}
    @media print {{
      [data-motion-controls], [data-motion-decorative] {{ display: none !important; }}
      [data-motion-item] {{ opacity: 1 !important; transform: none !important; animation: none !important; transition: none !important; }}
      pre.payload {{ max-height: none; }}
    }}
  </style>
</head>
<body>
  <main data-motion-root data-motion-mode="step" data-step-count="5" data-step-current="5" data-frame="static" data-static-frame="complete">
    <p class="eyebrow">Sequence · 逐轮回放 · Interactive</p>
    <h1>消息是怎么叠上去的</h1>
    <p class="lede">本地 Agent 与无状态 LLM 之间只有一次次 POST。<strong>整个 messages 数组由 Agent 在本地拼好、每轮整段重发</strong>；模型返回的工具调用被本地执行后，结果作为新消息追加到数组末尾。下面按五轮依次回放，每条消息都可以点开看它真实的 payload。</p>
    <p class="hint">Next ▶ / → 前进一轮 · Prev ◀ 后退 · Home / End 跳到首尾 · Enter 展开或收起消息的 payload · 无 JavaScript、打印、以及「减少动态效果」下整图与全部内容完整可见</p>

    {"".join(svg)}

    <div class="rounds">
    {sections_txt}
    </div>

    <div data-motion-controls role="group" aria-label="逐轮回放控件">
      <button type="button" data-motion-action="prev">◀ Prev</button>
      <button type="button" data-motion-action="play" aria-pressed="false">Play</button>
      <button type="button" data-motion-action="pause" aria-pressed="true">Pause</button>
      <button type="button" data-motion-action="next">Next ▶</button>
      <button type="button" data-motion-action="replay">Replay</button>
      <span>←/→ 逐轮 · Space 播放/暂停 · R 重放 · Home/End 首尾</span>
      <span data-motion-status-visible aria-hidden="true">Step <span data-motion-step-label>5</span> of 5</span>
    </div>
    <p class="sr-only" data-motion-status role="status" aria-live="polite" aria-atomic="true"></p>
    <noscript><p>逐轮回放控件需要 JavaScript；上面五轮的完整内容与全部消息 payload 已全部可见，可直接阅读。</p></noscript>

    <footer>
      数据：{run_dir.name}/ · 五轮请求体 {total_bytes[0]:,} B → {total_bytes[-1]:,} B（messages 2 → 5 → 7 → 9 → 11 条）·
      输入 token {sum(total_tok):,}（其中缓存命中 {sum(r["usage"].get("prompt_cache_hit_tokens", 0) for r in rounds):,}）·
      本地工程 quest-mvp-lab/llm-heartbeat-io-mvp/
    </footer>
  </main>

  {controller}
</body>
</html>
'''
    out.write_text(doc, encoding="utf-8")
    print(f"written: {out}  ({out.stat().st_size:,} bytes) · {len(rounds)} rounds · {len(order)} unique messages"
          f" · marked motion items {len(rounds) * 2}")


def main() -> int:
    run_dir = (pathlib.Path(sys.argv[1]).resolve() if len(sys.argv) > 1
               else HERE.parent / "runs/20260911_150720_conversation")
    out = pathlib.Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else HERE / "message-assembly.html"
    run_dir = pathlib.Path(run_dir).resolve()
    build(run_dir, out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
