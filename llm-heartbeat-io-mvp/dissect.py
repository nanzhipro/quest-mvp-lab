#!/usr/bin/env python3
"""Deep dissection of a heartbeat capture: input composition, output composition, tokens.

Usage
-----
    python3 dissect.py runs/20260911_143029

Writes dissect.json next to the run and prints a layered report:

  L0  exchange inventory (every HTTP call the client made)
  L1  the agent heartbeat: system-prompt anatomy, tool-schema anatomy, token truth
  L2  bare-model heartbeat: same for the minimal call
  L3  output anatomy: content / reasoning / finish_reason / usage / latency
"""

import json
import os
import re
import sys
from collections import OrderedDict


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def read_body(run_dir, seq, kind):
    for ext in ("json", "bin"):
        p = os.path.join(run_dir, "raw", f"{kind}_{seq:04d}.{ext}")
        if os.path.exists(p):
            return open(p, encoding="utf-8", errors="replace").read(), p
    return None, None


def parse_sse(text):
    events, usage, content, reasoning, finish, tool_chunks = [], None, "", "", None, 0
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload in ("[DONE]", ""):
            continue
        try:
            ev = json.loads(payload)
        except Exception:
            continue
        events.append(ev)
        if ev.get("usage"):
            usage = ev["usage"]
        for ch in ev.get("choices") or []:
            d = ch.get("delta") or {}
            content += d.get("content") or ""
            reasoning += d.get("reasoning_content") or ""
            if d.get("tool_calls"):
                tool_chunks += 1
            if ch.get("finish_reason"):
                finish = ch["finish_reason"]
    return {
        "sse_events": len(events),
        "content": content,
        "reasoning_content": reasoning,
        "finish_reason": finish,
        "tool_call_chunks": tool_chunks,
        "usage": usage,
    }


SECTION_MARKERS = [
    ("memory_notes", "MEMORY (your personal notes)"),
    ("user_profile", "USER PROFILE (who the user is)"),
    ("mem0", "# Mem0 Memory"),
    ("skills_catalogue", "<available_skills>"),
    ("workspace_snapshot", "Workspace (snapshot at session start"),
    ("project_context", "The following project context files have been loaded"),
    ("tools_dev_section", "## Tool-use enforcement"),
    ("finishing_job", "# Finishing the job"),
    ("parallel_tools", "# Parallel tool calls"),
    ("hermes_identity", "You are Hermes Agent"),
    ("docs_pointer", "hermes-agent.nousresearch.com/docs"),
    ("platform_tui", "You are in the Hermes terminal UI"),
    ("skill_safety", "## Skill Safety Rule"),
    ("midturn_steering", "## Mid-turn user steering"),
]


def section_anatomy(prompt):
    """Char cost of each recognisable block, in first-appearance order."""
    hits = []
    for key, marker in SECTION_MARKERS:
        idx = prompt.find(marker)
        if idx >= 0:
            hits.append((idx, key, marker))
    hits.sort()
    out = OrderedDict()
    for i, (idx, key, marker) in enumerate(hits):
        end = hits[i + 1][0] if i + 1 < len(hits) else len(prompt)
        out[key] = {"start_char": idx, "chars": end - idx, "marker": marker}
    covered = sum(v["chars"] for v in out.values())
    out["_unattributed_prefix"] = {"start_char": 0, "chars": hits[0][0] if hits else len(prompt)}
    out["_coverage_chars"] = covered
    out["_total_chars"] = len(prompt)
    return out


def tool_table(tools):
    rows = []
    for t in tools:
        fn = t.get("function") or t
        rows.append({
            "name": fn.get("name"),
            "chars": len(json.dumps(t, ensure_ascii=False)),
            "desc_chars": len(fn.get("description") or ""),
            "param_chars": len(json.dumps(fn.get("parameters") or {}, ensure_ascii=False)),
        })
    return sorted(rows, key=lambda r: -r["chars"])


def main():
    run_dir = sys.argv[1]
    events = [json.loads(l) for l in open(os.path.join(run_dir, "events.jsonl"), encoding="utf-8") if l.strip()]
    reqs = {r["seq"]: r for r in events if r["direction"] == "request"}
    resps = {r["seq"]: r for r in events if r["direction"] == "response"}

    report = {"run_dir": run_dir, "exchanges": [], "detail": {}}

    # ---------------------------------------------------------------- L0
    for seq in sorted(reqs):
        b = reqs[seq].get("body_json") or {}
        r = resps.get(seq, {})
        report["exchanges"].append({
            "seq": seq,
            "path": reqs[seq]["path"],
            "model": b.get("model"),
            "stream": b.get("stream"),
            "messages": len(b.get("messages") or []),
            "tools": len(b.get("tools") or []),
            "req_bytes": reqs[seq]["body_bytes"],
            "status": r.get("status"),
            "resp_bytes": r.get("body_bytes"),
            "latency_ms": r.get("latency_ms"),
            "purpose": None,
        })

    # ------------------------------------------------- L1 agent heartbeat
    agent = max((s for s in reqs if (reqs[s].get("body_json") or {}).get("tools")), default=None)
    if agent:
        b = reqs[agent]["body_json"]
        sysmsg = next((m["content"] for m in b["messages"] if m["role"] == "system"), "")
        sse = parse_sse(read_body(run_dir, agent, "resp")[0] or "")
        report["detail"]["agent_heartbeat"] = {
            "seq": agent,
            "user_message": [m["content"] for m in b["messages"] if m["role"] == "user"],
            "request_bytes": reqs[agent]["body_bytes"],
            "wire_params": {k: v for k, v in b.items() if k not in ("messages", "tools")},
            "system_prompt_chars": len(sysmsg),
            "system_prompt_lines": sysmsg.count("\n") + 1,
            "sections": section_anatomy(sysmsg),
            "tools": {"count": len(b["tools"]), "total_chars": len(json.dumps(b["tools"], ensure_ascii=False)),
                      "table": tool_table(b["tools"])},
            "usage": sse["usage"],
            "latency_ms": resps[agent]["latency_ms"],
            "output": {"content": sse["content"], "reasoning_chars": len(sse["reasoning_content"]),
                       "finish_reason": sse["finish_reason"], "sse_events": sse["sse_events"],
                       "resp_bytes": resps[agent]["body_bytes"]},
        }

    # ---------------------------------------------- L2 bare-model heartbeat
    bare = [s for s in sorted(reqs)
            if len((reqs[s].get("body_json") or {}).get("messages") or []) == 1]
    detail_bare = []
    for seq in bare:
        b = reqs[seq]["body_json"]
        text, path = read_body(run_dir, seq, "resp")
        if path.endswith(".json"):
            parsed = json.loads(text)
            ch = (parsed.get("choices") or [{}])[0]
            usage = parsed.get("usage")
            out = {"content": (ch.get("message") or {}).get("content"),
                   "reasoning_content": (ch.get("message") or {}).get("reasoning_content"),
                   "finish_reason": ch.get("finish_reason")}
        else:
            sse = parse_sse(text)
            usage = sse["usage"]
            out = {"content": sse["content"], "reasoning_content": sse["reasoning_content"],
                   "finish_reason": sse["finish_reason"]}
        detail_bare.append({
            "seq": seq, "stream": b.get("stream"), "request_bytes": reqs[seq]["body_bytes"],
            "request_body": b, "usage": usage, "output": out,
            "latency_ms": resps[seq]["latency_ms"], "resp_bytes": resps[seq]["body_bytes"],
        })
    report["detail"]["bare_heartbeats"] = detail_bare

    with open(os.path.join(run_dir, "dissect.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------- print
    print("#" * 78)
    print("L0  exchange inventory")
    print("#" * 78)
    for e in report["exchanges"]:
        print(f"  #{e['seq']:<3} {e['path']:<22} model={e['model']} msgs={e['messages']} tools={e['tools']:<3} "
              f"req={e['req_bytes']:>7,}B resp={e['resp_bytes']:>7,}B {e['latency_ms']}ms status={e['status']}")

    ah = report["detail"].get("agent_heartbeat")
    if ah:
        print()
        print("#" * 78)
        print(f"L1  agent heartbeat (#{ah['seq']}) — input anatomy")
        print("#" * 78)
        print(f"  user message verbatim: {ah['user_message']}")
        print(f"  wire params: {json.dumps(ah['wire_params'], ensure_ascii=False)}")
        print(f"  request bytes={ah['request_bytes']:,}  system_prompt={ah['system_prompt_chars']:,} chars "
              f"({ah['system_prompt_lines']:,} lines)  tools={ah['tools']['count']} "
              f"({ah['tools']['total_chars']:,} chars)")
        print("  system prompt sections (first-appearance order):")
        for k, v in ah["sections"].items():
            if isinstance(v, int):
                print(f"      {k:<24} {v:>8,}")
                continue
            print(f"      {k:<24} {v['chars']:>8,} chars")
        print(f"  headings inventory (offsets into system prompt):")
        sp = read_body(run_dir, ah["seq"], "req")[0]
        sysprompt = next(m for m in json.loads(sp)["messages"] if m["role"] == "system")["content"]
        off = 0
        for line in sysprompt.split("\n"):
            if re.match(r"^#{1,2} \S", line) or re.match(r"^<[a-z_]+>", line.strip()):
                print(f"      @{off:>7,}  {line[:96]}")
            off += len(line) + 1
        print("  tool schema size table (top 12):")
        for t in ah["tools"]["table"][:12]:
            print(f"    {t['name']:<34} total={t['chars']:>6,}  desc={t['desc_chars']:>5,}  params={t['param_chars']:>5,}")
        if ah["usage"]:
            u = ah["usage"]
            print(f"  usage(real): prompt={u.get('prompt_tokens'):,} completion={u.get('completion_tokens'):,} "
                  f"reasoning={(u.get('completion_tokens_details') or {}).get('reasoning_tokens')} "
                  f"cache_hit={u.get('prompt_cache_hit_tokens'):,} cache_miss={u.get('prompt_cache_miss_tokens'):,}")
        print(f"  latency={ah['latency_ms']}ms  response_bytes={ah['output']['resp_bytes']:,}  "
              f"sse_events={ah['output']['sse_events']}  finish={ah['output']['finish_reason']}")
        print(f"  content[{len(ah['output']['content'])}]: {ah['output']['content']!r}")
        print(f"  reasoning_chars={ah['output']['reasoning_chars']}")

    print()
    print("#" * 78)
    print("L2/L3  bare-model heartbeats")
    print("#" * 78)
    for b in detail_bare:
        u = b["usage"] or {}
        print(f"  #{b['seq']} stream={b['stream']} request={json.dumps(b['request_body'], ensure_ascii=False)}")
        print(f"      request_bytes={b['request_bytes']}  response_bytes={b['resp_bytes']:,}  latency={b['latency_ms']}ms")
        print(f"      usage: prompt={u.get('prompt_tokens')} completion={u.get('completion_tokens')} "
              f"reasoning={(u.get('completion_tokens_details') or {}).get('reasoning_tokens')} "
              f"cache_hit={u.get('prompt_cache_hit_tokens')}")
        print(f"      content: {b['output']['content']!r}")
        print(f"      finish={b['output']['finish_reason']}")
        rc = b["output"].get("reasoning_content") or ""
        if rc:
            print(f"      reasoning[{len(rc)}]: {rc[:200]!r}")
    print()
    print(f"written: {os.path.join(run_dir, 'dissect.json')}")


if __name__ == "__main__":
    main()
