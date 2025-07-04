from typing import overload

from tracecat.secrets import secrets_manager
from tracecat_registry._internal.exceptions import SecretNotFoundError


@overload
def get_or_default(name: str, /) -> str | None: ...


@overload
def get_or_default[T](name: str, default: T, /) -> str | T: ...


def get_or_default[T](name: str, default: T | None = None, /) -> str | T | None:
    """Lookup a secret set in the current context by name, or return a default value if not found."""
    return secrets_manager.get(name, default)


def get(name: str) -> str:
    """Lookup a secret set in the current context by name, or raise an error if not found."""
    if secret := secrets_manager.get(name, None):
        return secret
    raise SecretNotFoundError(f"Secret '{name}' is required but not found.")


def set(name: str, value: str, /) -> None:
    """Set a secret in the current context."""
    return secrets_manager.set(name, value)
