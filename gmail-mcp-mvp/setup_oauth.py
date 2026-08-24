"""One-shot OAuth bootstrap for the Gmail MCP server.

Mints secrets/token.json via Google's installed-app (loopback) flow with the
gmail.readonly scope. Requires an OAuth Desktop client created in Google Cloud
Console — see README.md for the exact click path.

Usage:
    uv run python setup_oauth.py                        # uses secrets/client_secret.json
    uv run python setup_oauth.py --client-json /path/x.json
    uv run python setup_oauth.py --client-id ... --client-secret ...   # env-style

The browser opens automatically; approve the consent screen, then the token is
saved and the server is ready. Re-run whenever the token expires (Testing-mode
apps: refresh tokens live ~7 days) or you want to switch accounts.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from gmail_client import SCOPES

PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_CLIENT_JSON = PROJECT_DIR / "secrets" / "client_secret.json"
DEFAULT_TOKEN_OUT = PROJECT_DIR / "secrets" / "token.json"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--client-json", type=Path, default=DEFAULT_CLIENT_JSON,
                   help="Path to the OAuth Desktop client JSON (default: secrets/client_secret.json)")
    p.add_argument("--client-id", default=None, help="OAuth client ID (alternative to --client-json)")
    p.add_argument("--client-secret", default=None, help="OAuth client secret (alternative to --client-json)")
    p.add_argument("--token-out", type=Path, default=DEFAULT_TOKEN_OUT, help="Where to write token.json")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    from google_auth_oauthlib.flow import InstalledAppFlow

    if args.client_id and args.client_secret:
        client_config = {
            "installed": {
                "client_id": args.client_id,
                "client_secret": args.client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        }
        flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    else:
        if not args.client_json.exists():
            raise SystemExit(
                f"Client credentials not found at {args.client_json}.\n"
                "Create a Desktop-type OAuth client in Google Cloud Console and download its "
                "JSON as secrets/client_secret.json (README.md, 快速开始 step 1)."
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(args.client_json), SCOPES)

    # port=0 picks a free loopback port; prompt=consent forces a fresh grant.
    creds = flow.run_local_server(port=0, prompt="consent")

    args.token_out.parent.mkdir(parents=True, exist_ok=True)
    args.token_out.write_text(json.dumps(json.loads(creds.to_json()), indent=2))
    print(f"\n✓ OAuth token saved to {args.token_out}")
    print("  Scopes:", ", ".join(creds.scopes))
    print("  Next:  restart Hermes (or `hermes mcp test gmail`) to use the server.")


if __name__ == "__main__":
    main()
