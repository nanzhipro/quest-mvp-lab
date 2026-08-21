"""Configuration and API-key management for aicode.

Key resolution order (first match wins):

1. ``AICODE_API_KEY`` environment variable — CI, one-off overrides.
2. macOS Keychain item (service ``aicode``, account ``api_key``) — only when
   the user opted in via ``aicode key --set --keychain``.
3. ``~/.config/aicode/api_key`` — plain file, mode 0600.

The API key never lives inside the repository or in shell history.
"""

import contextlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

# Defaults for the Bailian (Aliyun Model Studio) OpenAI-compatible gateway.
DEFAULT_BASE_URL = "https://ws-5z4aaxqg8o2sfw7b.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen3.8-27b"
DEFAULT_MAX_TOKENS = 4096
DEFAULT_MAX_ITERS = 20
DEFAULT_CMD_TIMEOUT = 60
DEFAULT_TIMEOUT = 300

KEYCHAIN_SERVICE = "aicode"
KEYCHAIN_ACCOUNT = "api_key"


def config_dir() -> Path:
    """Resolve the config directory (respects XDG_CONFIG_HOME)."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "aicode"


def key_file() -> Path:
    return config_dir() / "api_key"


def prefs_file() -> Path:
    return config_dir() / "config.json"


def load_prefs() -> dict:
    try:
        with open(prefs_file(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_prefs(prefs: dict) -> None:
    config_dir().mkdir(parents=True, exist_ok=True)
    tmp = prefs_file().with_name("config.json.tmp")
    tmp.write_text(json.dumps(prefs, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, prefs_file())


def _keychain_lookup() -> Optional[str]:
    security = shutil.which("security")
    if security is None:
        return None
    try:
        proc = subprocess.run(
            [
                security,
                "find-generic-password",
                "-s",
                KEYCHAIN_SERVICE,
                "-a",
                KEYCHAIN_ACCOUNT,
                "-w",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _keychain_store(key: str) -> None:
    security = shutil.which("security")
    if security is None:
        raise RuntimeError("`security` CLI not found — macOS Keychain unavailable")
    proc = subprocess.run(
        [
            security,
            "add-generic-password",
            "-U",
            "-s",
            KEYCHAIN_SERVICE,
            "-a",
            KEYCHAIN_ACCOUNT,
            "-w",
            key,
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if proc.returncode != 0:
        raise RuntimeError("Keychain store failed: {}".format(proc.stderr.strip()))


def _keychain_delete() -> None:
    security = shutil.which("security")
    if security is None:
        return
    subprocess.run(
        [security, "delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT],
        capture_output=True,
        timeout=10,
    )


def key_source() -> Optional[str]:
    """Return where the currently active key comes from, or None."""
    if os.environ.get("AICODE_API_KEY"):
        return "env"
    if load_prefs().get("keychain") and _keychain_lookup():
        return "keychain"
    try:
        stored = key_file().read_text(encoding="utf-8").strip()
    except OSError:
        stored = ""
    if stored:
        return "file"
    return None


def resolve_key() -> str:
    """Return the API key or raise RuntimeError with setup guidance."""
    source = key_source()
    if source == "env":
        return os.environ["AICODE_API_KEY"].strip()
    if source == "keychain":
        return _keychain_lookup()  # type: ignore[return-value]
    if source == "file":
        return key_file().read_text(encoding="utf-8").strip()
    raise RuntimeError("No API key configured. Set AICODE_API_KEY, or run `aicode key --set`.")


def set_key(key: str, use_keychain: bool = False) -> str:
    """Persist the key; returns a human-readable description of where it went."""
    key = key.strip()
    if not key:
        raise ValueError("empty API key")
    if use_keychain:
        _keychain_store(key)
        prefs = load_prefs()
        prefs["keychain"] = True
        save_prefs(prefs)
        return "macOS Keychain"
    config_dir().mkdir(parents=True, exist_ok=True)
    key_file().write_text(key + "\n", encoding="utf-8")
    os.chmod(key_file(), 0o600)
    return str(key_file())


def clear_key() -> None:
    _keychain_delete()
    with contextlib.suppress(OSError):
        key_file().unlink()
    prefs = load_prefs()
    prefs.pop("keychain", None)
    save_prefs(prefs)


def mask_key(key: str) -> str:
    if len(key) <= 8:
        return "*" * len(key)
    return "{}{}...{}".format(key[:5], "*" * 8, key[-4:])
