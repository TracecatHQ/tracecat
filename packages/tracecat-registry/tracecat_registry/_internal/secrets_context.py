"""Standalone secrets context for registry actions.

This module provides secrets management that works without importing tracecat.
Secrets can be injected via:
1. The context variable (set by executor via env_sandbox)
2. Environment variables prefixed with TRACECAT_SECRET_

The executor should set secrets before running actions using env_sandbox().
"""

from __future__ import annotations

import contextlib
import os
from contextvars import ContextVar
from typing import Iterator, overload

from tracecat_registry._internal.exceptions import SecretNotFoundError

# Context variable for secrets dict
# This is set by the executor before running actions
_ctx_secrets: ContextVar[dict[str, str] | None] = ContextVar(
    "registry_secrets", default=None
)

# Prefix for environment variable secrets
_ENV_SECRET_PREFIX = "TRACECAT_SECRET_"


def _get_secrets_dict() -> dict[str, str]:
    """Get the current secrets dict from context, or empty dict if not set."""
    return _ctx_secrets.get() or {}


@overload
def get(name: str, /) -> str | None: ...


@overload
def get[T](name: str, default: T, /) -> str | T: ...


def get[T](name: str, default: T | None = None, /) -> str | T | None:
    """Get a secret by name.

    Lookup order:
    1. Context variable (set by executor via env_sandbox)
    2. Environment variable with TRACECAT_SECRET_ prefix

    Args:
        name: Secret key name (e.g., "API_KEY")
        default: Default value if secret not found

    Returns:
        Secret value, or default if not found
    """
    # First check context variable
    secrets = _get_secrets_dict()
    if name in secrets:
        return secrets[name]

    # Fallback to environment variable
    env_name = f"{_ENV_SECRET_PREFIX}{name}"
    if env_value := os.environ.get(env_name):
        return env_value

    return default


def get_required(name: str) -> str:
    """Get a secret by name, raising if not found.

    Args:
        name: Secret key name

    Returns:
        Secret value

    Raises:
        SecretNotFoundError: If secret is not found
    """
    if value := get(name, None):
        return value
    raise SecretNotFoundError(f"Secret '{name}' is required but not found.")


def set(name: str, value: str, /) -> None:
    """Set a secret in the current context.

    Args:
        name: Secret key name
        value: Secret value
    """
    secrets = _ctx_secrets.get()
    if secrets is None:
        secrets = {}
        _ctx_secrets.set(secrets)
    secrets[name] = value


@contextlib.contextmanager
def env_sandbox(
    initial_secrets: dict[str, str] | None = None,
) -> Iterator[None]:
    """Create a sandboxed environment with isolated secrets.

    This context manager sets up a secrets environment, yields control,
    and resets to the original state on exit.

    Args:
        initial_secrets: Initial secrets dict to populate

    Yields:
        None

    Example:
        >>> with env_sandbox({"API_KEY": "secret123"}):
        ...     api_key = get("API_KEY")
        ...     # Use api_key...
    """
    initial_secrets = initial_secrets or {}
    token = _ctx_secrets.set(initial_secrets)
    try:
        yield
    finally:
        _ctx_secrets.reset(token)


def get_all() -> dict[str, str]:
    """Get all secrets from context.

    Returns:
        Copy of the current secrets dict
    """
    return _get_secrets_dict().copy()


def clear() -> None:
    """Clear all secrets from context."""
    _ctx_secrets.set(None)


def flatten_secrets(secrets: dict[str, dict[str, str]]) -> dict[str, str]:
    """Flatten nested secrets dict to a flat dict.

    Given secrets in the format {name: {key: value}}, flatten to {key: value}.

    For example, if you have the secret `my_secret.KEY`, then you access this
    in the UDF as `KEY`. This means you cannot have a clashing key in different secrets.

    Args:
        secrets: Nested secrets dict {secret_name: {key: value}}

    Returns:
        Flattened dict {key: value}

    Raises:
        ValueError: If there are duplicate keys across different secrets
    """
    flattened: dict[str, str] = {}
    for name, keyvalues in secrets.items():
        for key, value in keyvalues.items():
            if key in flattened:
                raise ValueError(
                    f"Key {key!r} is duplicated in {name!r}! "
                    "Please ensure only one secret with a given name is set. "
                    "e.g. If you have `first_secret.KEY` set, then you cannot "
                    "also set `second_secret.KEY` as `KEY` is duplicated."
                )
            flattened[key] = value
    return flattened
