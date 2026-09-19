#!/usr/bin/env python3
"""Recording reverse proxy for LLM wire-level capture.

Purpose
-------
Sit between an LLM client (curl, `hermes chat`, any SDK) and the real provider
endpoint. Every HTTP request body and every response body is written to a
timestamped run directory, so the *exact* bytes the client sent and the model
returned can be inspected later without re-running anything.

Usage
-----
    python3 capture_proxy.py --port 8899 --target https://api.deepseek.com \
        --run-dir runs/20260911_143000

    # then point a client at http://127.0.0.1:8899
    DEEPSEEK_BASE_URL=http://127.0.0.1:8899 hermes chat -q 'ping'

Output (inside --run-dir)
-------------------------
    events.jsonl       one JSON object per HTTP exchange (redacted headers)
    raw/req_000N.json  verbatim request body
    raw/resp_000N.json verbatim response body
    raw/req_000N.http  request headers + body (Authorization redacted)

Secrets: the Authorization header is never written to disk in clear text.
"""

import argparse
import hashlib
import json
import os
import ssl
import sys
import threading
import time
import http.client
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ---------------------------------------------------------------------------
# run-directory state
# ---------------------------------------------------------------------------

_LOCK = threading.Lock()
_SEQ = 0
ARGS = None


def _redact_headers(headers):
    out = {}
    for k, v in headers.items():
        if k.lower() in ("authorization", "x-api-key", "api-key", "cookie"):
            out[k] = f"<redacted len={len(v)} prefix={v[:7]}...>"
        else:
            out[k] = v
    return out


def _now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def _write_raw(seq, kind, payload: bytes, headers: dict):
    raw_dir = os.path.join(ARGS.run_dir, "raw")
    os.makedirs(raw_dir, exist_ok=True)
    ext = "json" if payload.lstrip()[:1] in (b"{", b"[") else "bin"
    body_path = os.path.join(raw_dir, f"{kind}_{seq:04d}.{ext}")
    with open(body_path, "wb") as fh:
        fh.write(payload)
    head_path = os.path.join(raw_dir, f"{kind}_{seq:04d}.http")
    with open(head_path, "w", encoding="utf-8") as fh:
        for k, v in headers.items():
            fh.write(f"{k}: {v}\n")
        fh.write("\n")
        fh.write(payload.decode("utf-8", errors="replace"))
    return body_path


def _log_event(record: dict):
    path = os.path.join(ARGS.run_dir, "events.jsonl")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# proxy handler
# ---------------------------------------------------------------------------

HOP_BY_HOP = {
    "host",
    "content-length",
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


class RecordingProxy(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "heartbeat-capture/1.0"

    def log_message(self, fmt, *a):  # silence default stderr chatter
        sys.stderr.write("[proxy] " + (fmt % a) + "\n")

    # -- helpers ----------------------------------------------------------
    def _forward(self, method: str, body: bytes = b""):
        global _SEQ
        with _LOCK:
            _SEQ += 1
            seq = _SEQ

        redacted = _redact_headers(self.headers)
        fwd = {}
        for k, v in self.headers.items():
            if k.lower() in HOP_BY_HOP:
                continue
            fwd[k] = v
        fwd["Accept-Encoding"] = "identity"  # keep response readable on disk
        if body:
            fwd["Content-Length"] = str(len(body))

        _write_raw(seq, "req", body, redacted)
        req_meta = {
            "seq": seq,
            "ts": _now_iso(),
            "direction": "request",
            "method": method,
            "path": self.path,
            "headers": redacted,
            "body_bytes": len(body),
            "body_sha256": hashlib.sha256(body).hexdigest(),
            "client": f"{self.client_address[0]}:{self.client_address[1]}",
        }
        try:
            req_meta["body_json"] = json.loads(body.decode("utf-8"))
        except Exception:
            req_meta["body_json"] = None
        _log_event(req_meta)

        target = ARGS.target.rstrip("/")
        scheme, _, hostpart = target.partition("://")
        if ":" in hostpart:
            host, port = hostpart.split(":", 1)
            port = int(port)
        else:
            host, port = hostpart, (443 if scheme == "https" else 80)

        t0 = time.time()
        status = None
        resp_headers = {}
        data = b""
        err = None
        try:
            if scheme == "https":
                conn = http.client.HTTPSConnection(
                    host, port, timeout=ARGS.timeout, context=ssl.create_default_context()
                )
            else:
                conn = http.client.HTTPConnection(host, port, timeout=ARGS.timeout)
            conn.request(method, self.path, body=body or None, headers=fwd)
            resp = conn.getresponse()
            status = resp.status
            resp_headers = dict(resp.getheaders())
            data = resp.read()
            conn.close()
        except Exception as exc:  # network failures still get recorded
            err = f"{type(exc).__name__}: {exc}"
        latency_ms = int((time.time() - t0) * 1000)

        resp_meta = {
            "seq": seq,
            "ts": _now_iso(),
            "direction": "response",
            "status": status,
            "headers": resp_headers,
            "body_bytes": len(data),
            "body_sha256": hashlib.sha256(data).hexdigest(),
            "latency_ms": latency_ms,
            "error": err,
        }
        try:
            resp_meta["body_json"] = json.loads(data.decode("utf-8"))
        except Exception:
            resp_meta["body_json"] = None  # e.g. SSE stream -> raw file only
        _write_raw(seq, "resp", data, resp_headers)
        _log_event(resp_meta)

        # reply to the client
        self.send_response(status or 502)
        for k, v in resp_headers.items():
            if k.lower() in ("content-length", "transfer-encoding", "connection"):
                continue
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if data:
            self.wfile.write(data)
        print(
            f"[proxy] #{seq} {method} {self.path} -> {status} "
            f"req={len(body)}B resp={len(data)}B {latency_ms}ms",
            flush=True,
        )

    # -- verbs ------------------------------------------------------------
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else b""
        self._forward("POST", body)

    def do_GET(self):
        self._forward("GET", b"")


def main():
    global ARGS
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--bind", default="127.0.0.1")
    ap.add_argument("--target", required=True, help="real provider base URL, e.g. https://api.deepseek.com")
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--timeout", type=int, default=600)
    ARGS = ap.parse_args()
    os.makedirs(os.path.join(ARGS.run_dir, "raw"), exist_ok=True)
    srv = ThreadingHTTPServer((ARGS.bind, ARGS.port), RecordingProxy)
    srv.daemon_threads = True
    print(f"[proxy] listening on http://{ARGS.bind}:{ARGS.port} -> {ARGS.target}  run={ARGS.run_dir}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
