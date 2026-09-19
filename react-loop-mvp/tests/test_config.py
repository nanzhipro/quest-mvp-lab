"""config.py — the key resolution rules, including the ones that must never leak."""

from __future__ import annotations

import pytest

from react_loop_mvp.config import (
    DEFAULT_BASE_URL,
    DEFAULT_MAX_RETRIES,
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT_S,
    Config,
    ConfigError,
)


def test_from_env_uses_environment() -> None:
    config = Config.from_env(env={"DEEPSEEK_API_KEY": "sk-env"})
    assert config.api_key == "sk-env"
    assert config.base_url == DEFAULT_BASE_URL
    assert config.model == DEFAULT_MODEL
    assert config.timeout_s == DEFAULT_TIMEOUT_S
    assert config.max_retries == DEFAULT_MAX_RETRIES


def test_explicit_arguments_override_environment() -> None:
    config = Config.from_env(
        api_key="sk-flag",
        base_url="https://gateway.internal/v1",
        model="deepseek-reasoner",
        timeout_s=5,
        max_retries=1,
        env={
            "DEEPSEEK_API_KEY": "sk-env",
            "DEEPSEEK_BASE_URL": "https://api.deepseek.com",
            "DEEPSEEK_MODEL": "deepseek-chat",
        },
    )
    assert (config.api_key, config.base_url, config.model) == (
        "sk-flag",
        "https://gateway.internal/v1",
        "deepseek-reasoner",
    )
    assert (config.timeout_s, config.max_retries) == (5.0, 1)


def test_empty_environment_key_falls_back_to_explicit_key() -> None:
    config = Config.from_env(api_key="sk-flag", env={"DEEPSEEK_API_KEY": "   "})
    assert config.api_key == "sk-flag"


def test_missing_key_is_a_config_error() -> None:
    with pytest.raises(ConfigError) as excinfo:
        Config.from_env(env={})
    assert "DEEPSEEK_API_KEY" in str(excinfo.value)


def test_blank_key_is_a_config_error() -> None:
    with pytest.raises(ConfigError):
        Config.from_env(env={"DEEPSEEK_API_KEY": "   "})


def test_endpoint_tolerates_trailing_slash() -> None:
    config = Config(api_key="sk-x", base_url="https://example.test/v1/")
    assert config.chat_completions_url == "https://example.test/v1/chat/completions"
    assert config.models_url == "https://example.test/v1/models"


def test_masked_key_never_reveals_the_secret() -> None:
    config = Config(api_key="sk-abcdefgh-1234")
    masked = config.masked_key()
    assert masked == "****1234"
    assert "abcdefgh" not in masked


def test_short_key_is_fully_masked() -> None:
    assert Config(api_key="abcd").masked_key() == "****"


def test_repr_does_not_contain_the_key() -> None:
    config = Config(api_key="sk-super-secret-value")
    assert "sk-super-secret-value" not in repr(config)
    assert "api_key" not in repr(config)
