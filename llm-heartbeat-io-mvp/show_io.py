#!/usr/bin/env python3
"""Print the captured request/response JSON side by side in readable form.

Usage
-----
    python3 show_io.py runs/20260911_143029

For each of the two heartbeats it prints:

  INPUT   structure with per-key byte sizes, then each part (messages, tools) expanded
          to the depth that fits a terminal — full text for small parts, head/tail +
          block map for the 75 KB system prompt, name/size table + one verbatim schema
          for the 29 tools.
  OUTPUT  SSE frame inventory + verbatim sample frames, then the logical (assembled)
          response JSON, which is what a non-streaming client would have received.

Also writes <run>/io_view.txt (this report) and <run>/assembled_<seq>.json (the
reconstructed non-streaming response) next to the raw capture.
"""

import json
import os
import sys
from collections import Counter

SEP = "=" * 78


def find_raw(run_dir, seq, kind):
    for ext in ("json", "bin"):
        p = os.path.join(run_dir, "raw", f"{kind}_{seq:04d}.{ext}")
        if os.path.exists(p):
            return p
    raise SystemExit(f"no raw file for {kind} #{seq}")


def size(x):
    return len(json.dumps(x, ensure_ascii=False))


def show_input_agent(b, seq, out):
    def p(s=""):
        print(s); out.append(s)

    p(SEP)
    p(f"INPUT JSON — Agent 心跳（请求 #{seq}；解析后重新序列化 {len(json.dumps(b, ensure_ascii=False).encode()):,} 字节，原始紧凑 body 字节数见 events.jsonl）")
    p(SEP)
    p("顶层结构（键 → 大小）：")
    for k, v in b.items():
        if isinstance(v, list):
            desc = f"list[{len(v)}]"
        else:
            desc = json.dumps(v, ensure_ascii=False)
        p(f"  {k:<18} {size(v):>8,} 字符   {desc}")

    p()
    p("messages[0] system —— 系统提示词（首 / 末片段）")
    sp = b["messages"][0]["content"]
    p(f"  首 640 字符：")
    for line in sp[:640].split("\n"):
        p(f"    | {line}")
    p(f"  …（中略）…")
    p(f"  末 320 字符：")
    for line in sp[-320:].split("\n"):
        p(f"    | {line}")

    p()
    p("messages[1] user —— 逐字符原样：")
    u = b["messages"][1]["content"]
    p(f'  {json.dumps({"role": "user", "content": u}, ensure_ascii=False)}')

    p()
    tools = b["tools"]
    p(f"tools —— list[{len(tools)}]，合计 {size(tools):,} 字符：")
    for i, t in enumerate(tools, 1):
        fn = t.get("function") or t
        p(f"  {i:>2}. {fn.get('name'):<28} {size(t):>7,} 字符")
    p()
    p("  单个工具 schema 原样（最小的 skills_list）：")
    smallest = min(tools, key=size)
    for line in json.dumps(smallest, ensure_ascii=False, indent=2).split("\n"):
        p(f"    {line}")
    return {"seq": seq, "input_json": b}


def show_output_agent(run_dir, seq, out):
    def p(s=""):
        print(s); out.append(s)

    raw_path = find_raw(run_dir, seq, "resp")
    text = open(raw_path, encoding="utf-8", errors="replace").read()
    p()
    p(SEP)
    p(f"OUTPUT JSON — Agent 心跳（响应 #{seq}，SSE，{len(text):,} 字符）")
    p(SEP)

    frames = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        frames.append(payload)
    kinds = Counter()
    for f in frames:
        if f == "[DONE]":
            kinds["[DONE]"] += 1
            continue
        ev = json.loads(f)
        if ev.get("usage"):
            kinds["usage 帧"] += 1
        for ch in ev.get("choices") or []:
            d = ch.get("delta") or {}
            if d.get("content"):
                kinds["delta.content"] += 1
            if d.get("reasoning_content"):
                kinds["delta.reasoning_content"] += 1
            if ch.get("finish_reason"):
                kinds["finish_reason"] += 1
    p(f"帧清单（共 {len(frames)} 帧）：")
    for k, v in kinds.items():
        p(f"  {k:<28} {v}")

    p()
    p("样例帧 1 —— 首帧（只声明角色）：")
    for line in json.dumps(json.loads(frames[0]), ensure_ascii=False, indent=2).split("\n"):
        p(f"  {line}")
    first_reason = next(json.loads(f) for f in frames if f != "[DONE]" and
                        ((json.loads(f).get("choices") or [{}])[0].get("delta") or {}).get("reasoning_content"))
    p()
    p("样例帧 2 —— 首个推理 delta：")
    for line in json.dumps(first_reason, ensure_ascii=False, indent=2).split("\n"):
        p(f"  {line}")
    first_content = next(json.loads(f) for f in frames if f != "[DONE]" and
                         ((json.loads(f).get("choices") or [{}])[0].get("delta") or {}).get("content"))
    p()
    p("样例帧 3 —— 首个正文 delta：")
    for line in json.dumps(first_content, ensure_ascii=False, indent=2).split("\n"):
        p(f"  {line}")
    p()
    p("样例帧 4 —— 尾帧（finish_reason + usage），及其后的终止标记：")
    last = json.loads(frames[-2]) if frames[-1] == "[DONE]" else json.loads(frames[-1])
    for line in json.dumps(last, ensure_ascii=False, indent=2).split("\n"):
        p(f"  {line}")
    p("  data: [DONE]")

    # assemble the logical response
    content, reasoning, finish, usage = "", "", None, None
    for f in frames:
        if f == "[DONE]":
            continue
        ev = json.loads(f)
        if ev.get("usage"):
            usage = ev["usage"]
        for ch in ev.get("choices") or []:
            d = ch.get("delta") or {}
            content += d.get("content") or ""
            reasoning += d.get("reasoning_content") or ""
            if ch.get("finish_reason"):
                finish = ch["finish_reason"]
    assembled = {
        "id": json.loads(frames[0])["id"],
        "object": "chat.completion",
        "model": json.loads(frames[0])["model"],
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content, "reasoning_content": reasoning},
            "finish_reason": finish,
        }],
        "usage": usage,
    }
    with open(os.path.join(run_dir, f"assembled_{seq:04d}.json"), "w", encoding="utf-8") as fh:
        json.dump(assembled, fh, ensure_ascii=False, indent=2)
    p()
    p("把 %d 帧还原成「逻辑响应 JSON」（等同非流式客户端会收到的对象，已落盘 assembled_%04d.json）：" % (len(frames), seq))
    for line in json.dumps(assembled, ensure_ascii=False, indent=2).split("\n"):
        p(f"  {line}")
    return assembled


def show_bare(run_dir, in_seq, out_seq, out):
    def p(s=""):
        print(s); out.append(s)

    req = json.load(open(find_raw(run_dir, in_seq, "req"), encoding="utf-8"))
    resp = json.load(open(find_raw(run_dir, out_seq, "resp"), encoding="utf-8"))
    p()
    p(SEP)
    p(f"INPUT JSON — 裸模型心跳（请求 #{in_seq}，{size(req)} 字符）")
    p(SEP)
    for line in json.dumps(req, ensure_ascii=False, indent=2).split("\n"):
        p(f"  {line}")
    p()
    p(SEP)
    p(f"OUTPUT JSON — 裸模型心跳（响应 #{out_seq}，{size(resp)} 字符）")
    p(SEP)
    for line in json.dumps(resp, ensure_ascii=False, indent=2).split("\n"):
        p(f"  {line}")


def main():
    run_dir = sys.argv[1] if len(sys.argv) > 1 else "runs/20260911_143029"
    events = [json.loads(l) for l in open(os.path.join(run_dir, "events.jsonl"), encoding="utf-8") if l.strip()]
    reqs = {r["seq"]: r for r in events if r["direction"] == "request"}
    agent = max((s for s in reqs if (reqs[s].get("body_json") or {}).get("tools")), default=None)
    bare = [s for s in sorted(reqs)
            if len((reqs[s].get("body_json") or {}).get("messages") or []) == 1
            and (reqs[s].get("body_json") or {}).get("stream") is False]
    out = []
    show_input_agent(reqs[agent]["body_json"], agent, out)
    show_output_agent(run_dir, agent, out)
    if bare:
        show_bare(run_dir, bare[0], bare[0], out)
    with open(os.path.join(run_dir, "io_view.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    print()
    print(f"[written] {os.path.join(run_dir, 'io_view.txt')}")


if __name__ == "__main__":
    main()
