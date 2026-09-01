"""CLI entry point: scan text with the local PromptGuard 2 model.

Usage:
    pgscan "ignore all previous instructions"
    echo "..." | pgscan --stdin
    pgscan --role tool "<tool output>" --json
"""

from __future__ import annotations

import argparse
import json
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pgscan",
        description="Scan text for jailbreak / prompt injection with local PromptGuard 2 (86M)",
    )
    parser.add_argument("text", nargs="?", help="text to scan (omit with --stdin)")
    parser.add_argument("--stdin", action="store_true", help="read text from stdin")
    parser.add_argument(
        "--engine",
        choices=["guard", "firewall"],
        default="guard",
        help="guard = raw PromptGuard pipeline; firewall = LlamaFirewall PromptGuardScanner",
    )
    parser.add_argument("--role", choices=["user", "tool"], default="user", help="message role (firewall engine)")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.stdin:
        text = sys.stdin.read()
    elif args.text is not None:
        text = args.text
    else:
        build_parser().error("provide text or --stdin")

    if args.engine == "firewall":
        from .firewall import FirewallGate

        verdict = FirewallGate().scan(text, role=args.role)
        payload = {"decision": verdict.decision, "score": round(verdict.score, 4)}
        blocked = verdict.blocked
    else:
        from .guard import PromptGuard

        verdict = PromptGuard().scan(text)
        payload = {
            "label": verdict.label,
            "malicious_score": round(verdict.malicious_score, 4),
            "latency_ms": round(verdict.latency_ms, 1),
            "truncated": verdict.truncated,
        }
        blocked = verdict.is_malicious

    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(payload)
    return 1 if blocked else 0


if __name__ == "__main__":
    sys.exit(main())
