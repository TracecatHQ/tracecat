"""Secrets access for registry actions.

This module provides the public API for accessing secrets in registry actions.
It uses the standalone secrets_context module which does not depend on tracecat.
"""

from typing import overload

from tracecat_registry._internal.exceptions import SecretNotFoundError
from tracecat_registry._internal.secrets_context import (
    env_sandbox,
    flatten_secrets,
    get as _get,
    get_all,
    get_required,
    set as _set,
)

__all__ = [
    "get",
    "get_or_default",
    "get_required",
    "set",
    "env_sandbox",
    "flatten_secrets",
    "get_all",
    "SecretNotFoundError",
]


@overload
def get_or_default(name: str, /) -> str | None: ...


@overload
def get_or_default[T](name: str, default: T, /) -> str | T: ...


def get_or_default[T](name: str, default: T | None = None, /) -> str | T | None:
    """Lookup a secret by name, or return a default value if not found.

    Args:
        name: Secret key name
        default: Default value if secret not found

    Returns:
        Secret value, or default if not found
    """
    return _get(name, default)


def get(name: str) -> str:
    """Lookup a secret by name, or raise an error if not found.

    Args:
        name: Secret key name

    Returns:
        Secret value

    Raises:
        SecretNotFoundError: If secret is not found
    """
    return get_required(name)


def set(name: str, value: str, /) -> None:
    """Set a secret in the current context.

    Args:
        name: Secret key name
        value: Secret value
    """
    return _set(name, value)
