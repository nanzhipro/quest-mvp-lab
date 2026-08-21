#!/usr/bin/env python3
"""API probe for Bailian (Alibaba Cloud Model Studio) OpenAI-compatible endpoint.

Reads the API key from ~/.config/aicode/api_key (0600), never from argv.
Prints: model list, one chat completion, one function-calling roundtrip.
"""

import json
import os
import sys
import urllib.error
import urllib.request

BASE_URL = "https://ws-5z4aaxqg8o2sfw7b.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
MODEL = "qwen3.8-27b"

KEY_FILE = os.path.expanduser("~/.config/aicode/api_key")


def load_key() -> str:
    key = os.environ.get("AICODE_API_KEY")
    if key:
        return key
    with open(KEY_FILE) as f:
        return f.read().strip()


def post(path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        BASE_URL + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {load_key()}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        print(f"HTTP {e.code}: {body[:2000]}", file=sys.stderr)
        raise


def get(path: str) -> dict:
    req = urllib.request.Request(
        BASE_URL + path,
        headers={"Authorization": f"Bearer {load_key()}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        print(f"HTTP {e.code}: {body[:2000]}", file=sys.stderr)
        raise


def main() -> int:
    print("== 1. models ==")
    models = get("/models")
    data = models.get("data", [])
    print(f"total models: {len(data)}")
    for m in data:
        print(" -", m.get("id"))
    ids = [m.get("id") for m in data]
    print("target model present:", MODEL in ids)

    print("\n== 2. chat completion ==")
    resp = post(
        "/chat/completions",
        {
            "model": MODEL,
            "messages": [{"role": "user", "content": "reply with exactly: pong"}],
            "max_tokens": 50,
        },
    )
    choice = resp["choices"][0]
    print("finish_reason:", choice.get("finish_reason"))
    print("content:", json.dumps(choice.get("message", {}), ensure_ascii=False)[:500])
    print("usage:", resp.get("usage"))

    print("\n== 3. function calling ==")
    resp = post(
        "/chat/completions",
        {
            "model": MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": "What files are in /tmp? Use list_dir.",
                }
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
            "max_tokens": 200,
        },
    )
    msg = resp["choices"][0]["message"]
    print("content:", json.dumps(msg.get("content"), ensure_ascii=False)[:300])
    print("tool_calls:", json.dumps(msg.get("tool_calls"), ensure_ascii=False)[:800])
    print("finish_reason:", resp["choices"][0].get("finish_reason"))

    print("\n== 4. tool result roundtrip ==")
    resp = post(
        "/chat/completions",
        {
            "model": MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": "List /tmp for me. Use list_dir.",
                },
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "list_dir",
                                "arguments": json.dumps({"path": "/tmp"}),
                            },
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call_1", "content": "file_a.txt\nfile_b.txt"},
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
            "max_tokens": 200,
        },
    )
    msg = resp["choices"][0]["message"]
    print("content:", json.dumps(msg.get("content"), ensure_ascii=False)[:500])
    print("finish_reason:", resp["choices"][0].get("finish_reason"))

    print("\nPROBE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
