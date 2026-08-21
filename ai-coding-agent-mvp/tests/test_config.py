"""Tests for aicode.config — key resolution, storage, and masking."""

import os
import stat

from aicode import config


def test_key_source_none_when_unconfigured(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("AICODE_API_KEY", raising=False)
    assert config.key_source() is None


def test_resolve_key_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("AICODE_API_KEY", "sk-env-key-12345")
    assert config.resolve_key() == "sk-env-key-12345"
    assert config.key_source() == "env"


def test_set_key_writes_0600_file(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("AICODE_API_KEY", raising=False)
    where = config.set_key("sk-file-key-99999")
    assert where.endswith("api_key")
    mode = os.stat(config.key_file()).st_mode
    assert stat.S_IMODE(mode) == 0o600
    assert config.resolve_key() == "sk-file-key-99999"
    assert config.key_source() == "file"


def test_set_key_rejects_empty():
    try:
        config.set_key("   ")
    except ValueError:
        return
    raise AssertionError("expected ValueError for empty key")


def test_clear_key(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("AICODE_API_KEY", raising=False)
    config.set_key("sk-temp-key")
    config.clear_key()
    assert config.key_source() is None
    try:
        config.resolve_key()
    except RuntimeError as err:
        assert "aicode key --set" in str(err)
        return
    raise AssertionError("expected RuntimeError after clear")


def test_resolve_key_missing_raises_guidance(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("AICODE_API_KEY", raising=False)
    try:
        config.resolve_key()
    except RuntimeError as err:
        assert "AICODE_API_KEY" in str(err)
        return
    raise AssertionError("expected RuntimeError")


def test_mask_key_short_and_long():
    assert config.mask_key("shortkey") == "*" * 8
    masked = config.mask_key("sk-ws-X.FAKEFAKEFAKEFAKEFAKEFAKEFAKE9")
    assert masked.startswith("sk-ws")
    assert masked.endswith("AKE9")
    assert "FAKEFAK" not in masked


def test_set_key_keychain_roundtrip(monkeypatch, tmp_path):
    """Keychain path stores the prefs flag and reads via a fake `security` CLI."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("AICODE_API_KEY", raising=False)

    fake = tmp_path / "security"
    fake.write_text("#!/bin/sh\nprintf 'sk-keychain-key-42'\n", encoding="utf-8")
    os.chmod(fake, 0o755)
    monkeypatch.setattr(config.shutil, "which", lambda _name: str(fake))

    where = config.set_key("sk-keychain-key-42", use_keychain=True)
    assert where == "macOS Keychain"
    assert config.load_prefs()["keychain"] is True
    assert config.resolve_key() == "sk-keychain-key-42"
    assert config.key_source() == "keychain"


def test_prefs_corrupt_file_is_tolerated(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    config.save_prefs({"keychain": True})
    config.prefs_file().write_text("{not json", encoding="utf-8")
    assert config.load_prefs() == {}
