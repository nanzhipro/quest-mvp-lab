"""Runtime configuration — assembled from the environment, overridable by CLI flags.

Three rules this module enforces, borrowed from the sibling MVPs because they keep
evidence honest:

* **No secret ever lives in the repository.** The key is read from
  ``DEEPSEEK_API_KEY``; there is no config file, no keychain lookup, no default.
* **The CLI only overrides, never invents.** A flag set by the user wins; every
  other value falls back to the environment and then to a documented default.
* **The key does not leak into logs.** ``api_key`` is excluded from ``repr`` and
  ``masked_key()`` returns at most the last four characters.

The Supervisor is meant to run on a *lightweight* model: it makes three short,
schema-constrained calls per request, so the default here is the small fast chat
model rather than a reasoning model. ``--model`` overrides it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, Optional

ENV_API_KEY = "DEEPSEEK_API_KEY"
ENV_BASE_URL = "DEEPSEEK_BASE_URL"
ENV_MODEL = "DEEPSEEK_MODEL"

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_TIMEOUT_S = 60.0
DEFAULT_MAX_RETRIES = 3

#: Planning budgets. They live here so a run's cost ceiling is a single visible number.
DEFAULT_MAX_SUPERSTEPS = 24
DEFAULT_MAX_REPAIRS = 1


class ConfigError(RuntimeError):
    """Raised when a runnable configuration cannot be assembled."""


@dataclass(frozen=True)
class Config:
    """Everything a run needs: credentials, endpoint, model, transport budget."""

    api_key: str = field(repr=False)
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    timeout_s: float = DEFAULT_TIMEOUT_S
    max_retries: int = DEFAULT_MAX_RETRIES
    max_supersteps: int = DEFAULT_MAX_SUPERSTEPS
    max_repairs: int = DEFAULT_MAX_REPAIRS

    def endpoint(self, path: str) -> str:
        """Join ``base_url`` with an API path, tolerating a trailing slash."""
        return "{}{}".format(self.base_url.rstrip("/"), path)

    @property
    def chat_completions_url(self) -> str:
        return self.endpoint("/chat/completions")

    @property
    def models_url(self) -> str:
        return self.endpoint("/models")

    def masked_key(self) -> str:
        """A safe representation of the key, for logs and probe output."""
        if len(self.api_key) <= 4:
            return "****"
        return "****{}".format(self.api_key[-4:])

    @classmethod
    def from_env(
        cls,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout_s: Optional[float] = None,
        max_retries: Optional[int] = None,
        max_supersteps: Optional[int] = None,
        max_repairs: Optional[int] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> "Config":
        """Build a config: explicit argument > environment > default.

        Raises:
            ConfigError: when no API key can be found (the only hard requirement).
        """
        source = os.environ if env is None else env
        resolved_key = (api_key or source.get(ENV_API_KEY, "")).strip()
        if not resolved_key:
            raise ConfigError(
                "no API key found; export {0}=sk-... (optionally {1}, {2})".format(
                    ENV_API_KEY, ENV_BASE_URL, ENV_MODEL
                )
            )
        return cls(
            api_key=resolved_key,
            base_url=(base_url or source.get(ENV_BASE_URL) or DEFAULT_BASE_URL).strip(),
            model=(model or source.get(ENV_MODEL) or DEFAULT_MODEL).strip(),
            timeout_s=DEFAULT_TIMEOUT_S if timeout_s is None else float(timeout_s),
            max_retries=DEFAULT_MAX_RETRIES if max_retries is None else int(max_retries),
            max_supersteps=(
                DEFAULT_MAX_SUPERSTEPS if max_supersteps is None else int(max_supersteps)
            ),
            max_repairs=DEFAULT_MAX_REPAIRS if max_repairs is None else int(max_repairs),
        )
