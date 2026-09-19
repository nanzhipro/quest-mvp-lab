#!/usr/bin/env python3
"""Analyse a capture_proxy.py run directory: dissect input composition and output.

Usage
-----
    python3 analyze.py runs/20260911_143000

Reads runs/<ts>/events.jsonl, pairs request/response records, and writes
runs/<ts>/summary.json plus a human-readable breakdown on stdout:

  * per-exchange input anatomy: system prompt chars, tool schema chars,
    message-array chars, tool count, message count/roles
  * ground-truth token counts from the provider's own `usage` block
  * output anatomy: content, reasoning_content, finish_reason, latency, tok/s
"""

import json
import os
import sys
from collections import Counter


def load_events(run_dir):
    records = []
    with open(os.path.join(run_dir, "events.jsonl"), encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def toolbar(tools):
    out = []
    for t in tools or []:
        fn = (t.get("function") or t)
        blob = json.dumps(t, ensure_ascii=False)
        out.append({"name": fn.get("name"), "chars": len(blob)})
    return sorted(out, key=lambda x: -x["chars"])


def parse_sse(text):
    """Best-effort parse of an OpenAI-style SSE stream into events + usage."""
    events = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            events.append({"done": True})
            continue
        try:
            events.append(json.loads(payload))
        except Exception:
            events.append({"unparsed": payload[:200]})
    return events


def analyze_exchange(req, resp, raw_dir, seq):
    body = req.get("body_json") or {}
    info = {
        "seq": seq,
        "path": req.get("path"),
        "model": body.get("model"),
        "stream": body.get("stream"),
        "temperature": body.get("temperature"),
        "max_tokens": body.get("max_tokens"),
        "request_bytes": req.get("body_bytes"),
        "response_bytes": resp.get("body_bytes") if resp else None,
        "latency_ms": resp.get("latency_ms") if resp else None,
        "http_status": resp.get("status") if resp else None,
    }

    # ---- input anatomy ---------------------------------------------------
    msgs = body.get("messages") or []
    roles = Counter(m.get("role") for m in msgs)
    sys_chars = sum(
        len(m.get("content") or "") for m in msgs if m.get("role") == "system"
    )
    tools = body.get("tools") or []
    tools_chars = len(json.dumps(tools, ensure_ascii=False)) if tools else 0
    conv_chars = sum(
        len(json.dumps(m, ensure_ascii=False)) for m in msgs if m.get("role") != "system"
    )
    info["input"] = {
        "message_count": len(msgs),
        "roles": dict(roles),
        "system_prompt_chars": sys_chars,
        "conversation_chars": conv_chars,
        "tool_count": len(tools),
        "tool_schema_chars": tools_chars,
        "tool_schema_share_pct": round(100.0 * tools_chars / max(1, tools_chars + sys_chars + conv_chars), 1),
        "total_message_chars": sys_chars + conv_chars,
        "user_messages": [m.get("content") for m in msgs if m.get("role") == "user"][:5],
        "top_tools_by_chars": toolbar(tools)[:12],
    }

    # ---- output anatomy --------------------------------------------------
    out = {}
    raw_resp_path = os.path.join(raw_dir, f"resp_{seq:04d}.json")
    if os.path.exists(raw_resp_path):
        text = open(raw_resp_path, encoding="utf-8", errors="replace").read()
        parsed = None
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = None
        if parsed is not None:
            choice = (parsed.get("choices") or [{}])[0]
            msg = choice.get("message") or {}
            out = {
                "wire_format": "json",
                "content": msg.get("content"),
                "reasoning_content": msg.get("reasoning_content"),
                "finish_reason": choice.get("finish_reason"),
                "usage": parsed.get("usage"),
                "id": parsed.get("id"),
                "created": parsed.get("created"),
            }
        else:
            events = parse_sse(text)
            content, reasoning, finish, usage, deltas = "", "", None, None, 0
            tool_calls = []
            for ev in events:
                if ev.get("done"):
                    continue
                for ch in ev.get("choices") or []:
                    d = ch.get("delta") or {}
                    if d.get("content"):
                        content += d["content"]
                        deltas += 1
                    if d.get("reasoning_content"):
                        reasoning += d["reasoning_content"]
                    if d.get("tool_calls"):
                        tool_calls.append(d["tool_calls"])
                    if ch.get("finish_reason"):
                        finish = ch["finish_reason"]
                if ev.get("usage"):
                    usage = ev["usage"]
            out = {
                "wire_format": "sse",
                "sse_event_count": len(events),
                "content": content,
                "reasoning_content": reasoning,
                "finish_reason": finish,
                "tool_call_chunks": len(tool_calls),
                "usage": usage,
            }
    info["output"] = out

    u = out.get("usage") or {}
    if out.get("usage"):
        pt = u.get("prompt_tokens") or 0
        info["tokens"] = {
            "prompt": pt,
            "completion": u.get("completion_tokens"),
            "reasoning": u.get("completion_tokens_details", {}).get("reasoning_tokens")
            if isinstance(u.get("completion_tokens_details"), dict)
            else None,
            "cache_hit": u.get("prompt_cache_hit_tokens"),
            "cache_miss": u.get("prompt_cache_miss_tokens"),
            "prompt_token_source": "provider usage block",
        }
    info["_server"] = {"req": req, "resp": resp}
    return info


def main():
    run_dir = sys.argv[1]
    raw_dir = os.path.join(run_dir, "raw")
    records = load_events(run_dir)
    pairs = []
    for i, rec in enumerate(records):
        if rec["direction"] != "request":
            continue
        resp = next(
            (r for r in records[i + 1 :] if r["direction"] == "response" and r["seq"] == rec["seq"]),
            None,
        )
        pairs.append(analyze_exchange(rec, resp, raw_dir, rec["seq"]))

    summary = {"run_dir": run_dir, "exchange_count": len(pairs), "exchanges": pairs}
    with open(os.path.join(run_dir, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)

    # ---- human-readable report ------------------------------------------
    for ex in pairs:
        i, o = ex["input"], ex["output"]
        print("=" * 78)
        print(f"#{ex['seq']}  POST {ex['path']}  model={ex['model']}  stream={ex['stream']}  "
              f"status={ex['http_status']}  {ex['latency_ms']}ms")
        print(f"  IN  bytes={ex['request_bytes']:,}  messages={i['message_count']} {i['roles']}  "
              f"tools={i['tool_count']}")
        print(f"      system_prompt={i['system_prompt_chars']:,} chars  "
              f"conversation={i['conversation_chars']:,} chars  "
              f"tool_schemas={i['tool_schema_chars']:,} chars "
              f"({i['tool_schema_share_pct']}% of input text)")
        if ex.get("tokens"):
            t = ex["tokens"]
            print(f"      tokens(prompt)={t['prompt']:,}  completion={t['completion']}  "
                  f"reasoning={t['reasoning']}  cache_hit={t['cache_hit']}  cache_miss={t['cache_miss']}")
        print(f"  OUT bytes={ex['response_bytes']:,}  wire={o.get('wire_format')}  "
              f"finish={o.get('finish_reason')}")
        c = (o.get("content") or "").replace("\n", "\\n")
        print(f"      content[{len(o.get('content') or '')} chars]: {c[:400]}")
        if o.get("reasoning_content"):
            r = o["reasoning_content"].replace("\n", "\\n")
            print(f"      reasoning[{len(o['reasoning_content'])} chars]: {r[:300]}")
        if ex["seq"] == 1 and i["tool_count"]:
            print("      top tools by schema size:",
                  ", ".join(f"{t['name']}({t['chars']})" for t in i["top_tools_by_chars"]))
    print("=" * 78)
    print(f"summary written: {os.path.join(run_dir, 'summary.json')}")


if __name__ == "__main__":
    main()
