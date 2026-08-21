#!/usr/bin/env python3
"""Probe: does the Bailian compatible endpoint support stream=true (SSE)?

Also measures how long a non-streaming long-ish generation takes.
Prints per-chunk shapes for content / reasoning_content / tool_calls deltas.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE_URL = "https://ws-5z4aaxqg8o2sfw7b.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
MODEL = "qwen3.8-27b"


def load_key():
    if os.environ.get("AICODE_API_KEY"):
        return os.environ["AICODE_API_KEY"]
    with open(os.path.expanduser("~/.config/aicode/api_key"), encoding="utf-8") as f:
        return f.read().strip()


KEY = load_key()


def post(payload, timeout=300):
    req = urllib.request.Request(
        BASE_URL + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json"},
        method="POST",
    )
    return urllib.request.urlopen(req, timeout=timeout)


def main():
    print("== 1. streaming (SSE) with tools ==")
    payload = {
        "model": MODEL,
        "stream": True,
        "messages": [
            {"role": "user", "content": "List the current directory using the list_dir tool."}
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "list_dir",
                    "description": "List directory entries",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                    },
                },
            }
        ],
        "max_tokens": 300,
    }
    t0 = time.time()
    seen = {"content": 0, "reasoning": 0, "tool_call_frag": 0, "other": []}
    finish = None
    tool_acc = {}
    try:
        resp = post(payload)
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            chunk = json.loads(data)
            choice = (chunk.get("choices") or [{}])[0]
            finish = choice.get("finish_reason") or finish
            delta = choice.get("delta") or {}
            if delta.get("content"):
                seen["content"] += len(delta["content"])
            if delta.get("reasoning_content"):
                seen["reasoning"] += len(delta["reasoning_content"])
            for tc in delta.get("tool_calls") or []:
                seen["tool_call_frag"] += 1
                idx = tc.get("index", 0)
                acc = tool_acc.setdefault(idx, {"id": "", "name": "", "args": ""})
                acc["id"] += tc.get("id") or ""
                if tc.get("function"):
                    acc["name"] += tc["function"].get("name") or ""
                    acc["args"] += tc["function"].get("arguments") or ""
    except urllib.error.HTTPError as e:
        print("HTTP {}: {}".format(e.code, e.read().decode()[:300]))
        return 1
    print("elapsed {:.1f}s, finish={}".format(time.time() - t0, finish))
    keys = ("content", "reasoning", "tool_call_frag")
    print("chars: content={} reasoning={} tool_frags={}".format(*[seen[k] for k in keys]))
    print("accumulated tool_calls:", json.dumps(tool_acc, ensure_ascii=False)[:300])

    print("\n== 2. non-streaming long generation timing ==")
    payload = {
        "model": MODEL,
        "messages": [
            {
                "role": "user",
                "content": (
                    "Write a complete Python flappy bird game using only stdlib (no pygame). "
                    "Full runnable code, no explanation."
                ),
            }
        ],
        "max_tokens": 2048,
    }
    t0 = time.time()
    try:
        resp = post(payload, timeout=600)
        body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print("HTTP {}: {}".format(e.code, e.read().decode()[:300]))
        return 1
    except TimeoutError:
        print("NON-STREAMING TIMED OUT after {:.1f}s".format(time.time() - t0))
        return 1
    msg = body["choices"][0]["message"]
    print(
        "elapsed {:.1f}s finish={} usage={}".format(
            time.time() - t0, body["choices"][0]["finish_reason"], body.get("usage")
        )
    )
    print("content len:", len(msg.get("content") or ""))
    print("reasoning len:", len(msg.get("reasoning_content") or ""))
    print("\nSTREAM PROBE DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
