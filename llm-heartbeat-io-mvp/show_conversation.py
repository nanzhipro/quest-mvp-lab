#!/usr/bin/env python3
"""Render a captured conversation: every request/response, formatted as JSON.

Usage
-----
    python3 show_conversation.py runs/20260911_150000_conversation

Reads the proxy capture in <run>/events.jsonl, reassembles each streaming response
(content + tool_calls merged from SSE deltas) and prints the agent loop turn by turn:

    请求 N   → model / stream / tools / messages[]   (system prompt elided after turn 1)
    响应 N   → content / tool_calls / finish_reason / usage
    工具结果 → 作为下一条请求里的 role="tool" 消息出现

Writes two files next to the raw capture:

    conversation.json       完整记录（含每回合完整 system prompt），原文可复核
    conversation_view.json  展示视图（system prompt 用标记替代，其余全量）
"""

import hashlib
import json
import os
import sys

SYS_MARK = {"role": "system", "content": "<ELIDED: identical system prompt, see conversation.json / raw/req_*.json>"}
CAP = 3000  # chars of a single message shown before eliding


def load(run_dir):
    path = os.path.join(run_dir, "events.jsonl")
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def raw_path(run_dir, seq, kind):
    for ext in ("json", "bin"):
        p = os.path.join(run_dir, "raw", f"{kind}_{seq:04d}.{ext}")
        if os.path.exists(p):
            return p
    return None


def assemble_sse(text):
    """Merge SSE deltas into one chat.completion-shaped object."""
    out = {"content": "", "reasoning": "", "tool_calls": {}, "finish_reason": None,
           "usage": None, "frames": 0, "id": None, "model": None}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload in ("", "[DONE]"):
            continue
        ev = json.loads(payload)
        out["frames"] += 1
        out["id"] = out["id"] or ev.get("id")
        out["model"] = out["model"] or ev.get("model")
        if ev.get("usage"):
            out["usage"] = ev["usage"]
        for ch in ev.get("choices") or []:
            d = ch.get("delta") or {}
            out["content"] += d.get("content") or ""
            out["reasoning"] += d.get("reasoning_content") or ""
            for tc in d.get("tool_calls") or []:
                idx = tc.get("index", 0)
                slot = out["tool_calls"].setdefault(idx, {"id": None, "type": "function",
                                                          "function": {"name": "", "arguments": ""}})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                slot["function"]["name"] += fn.get("name") or ""
                slot["function"]["arguments"] += fn.get("arguments") or ""
            if ch.get("finish_reason"):
                out["finish_reason"] = ch["finish_reason"]
    out["tool_calls"] = [out["tool_calls"][k] for k in sorted(out["tool_calls"])]
    return out


def elide(obj, cap=CAP):
    """Copy of obj with any oversized 'content' replaced by a marker."""
    o = json.loads(json.dumps(obj, ensure_ascii=False))
    if isinstance(o.get("content"), str) and len(o["content"]) > cap:
        n = len(o["content"])
        o["content"] = o["content"][:cap] + f"\n<ELIDED {n - cap} of {n} chars — full text in conversation.json>"
    if isinstance(o.get("reasoning_content"), str) and len(o["reasoning_content"]) > cap:
        o["reasoning_content"] = o["reasoning_content"][:cap] + f"\n<ELIDED …>"
    return o


def pj(obj, out, indent=""):
    for line in json.dumps(obj, ensure_ascii=False, indent=2).split("\n"):
        out.append(indent + line)


def main():
    run_dir = sys.argv[1] if len(sys.argv) > 1 else "runs"
    events = load(run_dir)
    reqs = {r["seq"]: r for r in events if r["direction"] == "request"}
    resps = {r["seq"]: r for r in events if r["direction"] == "response"}
    chat = [s for s in sorted(reqs) if (reqs[s].get("body_json") or {}).get("messages") is not None]

    full, view, out = [], [], []
    last_messages = None

    def say(s=""):
        out.append(s)

    say("=" * 78)
    say(f"会话记录 — {os.path.basename(run_dir)}（{len(chat)} 次推理调用）")
    say("=" * 78)

    prev_sys = None
    prev_n_by_sys = {}
    tool_calls_seen, skills_seen = [], []
    for turn, seq in enumerate(chat, 1):
        body = reqs[seq]["body_json"]
        msgs = body["messages"]
        sys_msg = next((m for m in msgs if m.get("role") == "system"), None)
        sys_hash = hashlib.sha256((sys_msg or {}).get("content", "").encode()).hexdigest()[:16]
        same = (sys_hash == prev_sys)
        prev_sys = sys_hash

        resp_path = raw_path(run_dir, seq, "resp")
        resp = assemble_sse(open(resp_path, encoding="utf-8", errors="replace").read()) if resp_path else None
        if resp:
            for tc in resp["tool_calls"]:
                name = tc["function"]["name"]
                tool_calls_seen.append(name)
                if name in ("skill_view", "skills_list", "skill_manage"):
                    try:
                        a = json.loads(tc["function"]["arguments"] or "{}")
                        skills_seen.append(a.get("name") or a.get("category") or a.get("action"))
                    except Exception:
                        skills_seen.append(name)

        full.append({
            "exchange": seq,
            "request": {k: v for k, v in body.items() if k != "messages"},
            "messages": msgs,
            "response": None if resp is None else {
                "content": resp["content"], "reasoning_content": resp["reasoning"],
                "tool_calls": resp["tool_calls"], "finish_reason": resp["finish_reason"],
                "usage": resp["usage"], "sse_frames": resp["frames"]},
        })
        start = prev_n_by_sys.get(sys_hash, 0)
        new = msgs[start:] if len(msgs) >= start else msgs
        view.append({
            "exchange": seq,
            "request": {k: v for k, v in body.items() if k != "messages"},
            "messages_repeated_prefix": max(0, len(msgs) - len(new)),
            "messages_new": [SYS_MARK if m is sys_msg else elide(m) for m in new],
            "response": None if resp is None else elide({
                "content": resp["content"], "reasoning_content": resp["reasoning"],
                "tool_calls": resp["tool_calls"], "finish_reason": resp["finish_reason"],
                "usage": resp["usage"], "sse_frames": resp["frames"]}),
        })
        prev_n_by_sys[sys_hash] = len(msgs)
        last_messages = msgs

        say()
        say(f"───── 第 {turn} 次推理调用（exchange #{seq}） ─────")
        sys_len = len((sys_msg or {}).get("content", ""))
        kind = "Agent 回合" if sys_len > 5000 else "辅助调用（会话标题生成）"
        say(f"性质：{kind}")
        say(f"请求参数：{json.dumps({k: v for k, v in body.items() if k not in ('messages', 'tools')}, ensure_ascii=False)}")
        say(f"工具数：{len(body.get('tools') or [])}    messages：{len(msgs)} 条"
            f"    system prompt {len((sys_msg or {}).get('content','')):,} 字符"
            f"（sha256[:16]={sys_hash}{'，与上一回合相同' if same else ''}）")
        if len(msgs) > len(new):
            say(f"前 {len(msgs) - len(new)} 条消息与上一回合完全一致（OpenAI 协议要求整段重发），下面只列本回合新增的 {len(new)} 条")
        if not resp:
            say("响应：<未捕获 —— 进程结束时该请求仍在飞行中>")
            continue
        say()
        say("请求 JSON（本回合新增的 messages）：")
        pj([SYS_MARK if m is sys_msg else elide(m) for m in new], out, "  ")
        say()
        say(f"响应 JSON（{resp['frames']} 帧 SSE 合并）：")
        pj(elide({"id": resp["id"], "model": resp["model"], "object": "chat.completion.chunk",
                  "choices": [{"index": 0,
                               "message": {"role": "assistant", "content": resp["content"],
                                           "reasoning_content": resp["reasoning"],
                                           **({"tool_calls": resp["tool_calls"]} if resp["tool_calls"] else {})},
                               "finish_reason": resp["finish_reason"]}],
                  "usage": resp["usage"]}), out, "  ")

    say()
    say("=" * 78)
    say("汇总")
    say("=" * 78)
    say(f"推理调用次数：{len(chat)}    工具调用：{len(tool_calls_seen)} 次 → {', '.join(tool_calls_seen) or '（无）'}")
    say(f"skill 相关调用：{', '.join(str(s) for s in skills_seen) or '（无）'}")
    total_in = sum(((v["response"] or {}).get("usage") or {}).get("prompt_tokens", 0) for v in full)
    total_out = sum(((v["response"] or {}).get("usage") or {}).get("completion_tokens", 0) for v in full)
    say(f"累计 prompt_tokens：{total_in:,}    completion_tokens：{total_out:,}")

    with open(os.path.join(run_dir, "conversation.json"), "w", encoding="utf-8") as fh:
        json.dump(full, fh, ensure_ascii=False, indent=2)
    with open(os.path.join(run_dir, "conversation_view.json"), "w", encoding="utf-8") as fh:
        json.dump(view, fh, ensure_ascii=False, indent=2)
    if last_messages:
        with open(os.path.join(run_dir, "session_messages.json"), "w", encoding="utf-8") as fh:
            json.dump(last_messages, fh, ensure_ascii=False, indent=2)
        out.append("")
        out.append(f"[written] session_messages.json —— 会话最后一条请求的完整 messages 数组（{len(last_messages)} 条），即模型最终看到的全部上下文")
    out.append("")
    out.append(f"[written] {os.path.join(run_dir, 'conversation.json')}")
    out.append(f"[written] {os.path.join(run_dir, 'conversation_view.json')}")
    text = "\n".join(out)
    print(text)
    with open(os.path.join(run_dir, "conversation.txt"), "w", encoding="utf-8") as fh:
        fh.write(text + "\n")


if __name__ == "__main__":
    main()
