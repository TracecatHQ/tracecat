import contextvars
import os
from typing import overload

from tracecat_registry._internal.exceptions import SecretNotFoundError
from tracecat_registry.config import flags

# Registry-owned secrets context
_secrets_ctx: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar(
    "registry_secrets_ctx", default={}
)


def init_from_env(keys: list[str] | None = None) -> None:
    """Initialize secrets context from environment variables.

    Called by executor before running UDF in sandbox mode.
    If keys is None, copies all environment variables.
    Otherwise, only copies the specified keys.
    """
    if keys is None:
        _secrets_ctx.set(dict(os.environ))
    else:
        _secrets_ctx.set({k: os.environ[k] for k in keys if k in os.environ})


def set_context(secrets: dict[str, str]) -> None:
    """Set the secrets context directly.

    Called by executor to initialize registry secrets before running UDFs.
    """
    _secrets_ctx.set(secrets)


# Overload declarations for type checking
@overload
def get_or_default(name: str, /) -> str | None: ...


@overload
def get_or_default[T](name: str, default: T, /) -> str | T: ...


def get_or_default[T](name: str, default: T | None = None, /) -> str | T | None:
    """Lookup a secret by name, or return a default value if not found.

    In sandbox/SDK mode, reads from the registry-owned secrets context.
    In direct mode, uses the secrets_manager context variable system.
    """
    if flags.registry_client:
        return _secrets_ctx.get().get(name, default)
    # Import lazily to avoid heavy tracecat imports in sandbox mode
    from tracecat.secrets import secrets_manager

    return secrets_manager.get(name, default)


def get(name: str) -> str:
    """Lookup a secret by name, or raise an error if not found.

    In sandbox/SDK mode, reads from the registry-owned secrets context.
    In direct mode, uses the secrets_manager context variable system.
    """
    if flags.registry_client:
        ctx = _secrets_ctx.get()
        if name in ctx:
            return ctx[name]
        raise SecretNotFoundError(f"Secret '{name}' is required but not found.")
    # Import lazily to avoid heavy tracecat imports in sandbox mode
    from tracecat.secrets import secrets_manager

    if secret := secrets_manager.get(name, None):
        return secret
    raise SecretNotFoundError(f"Secret '{name}' is required but not found.")


def set(name: str, value: str, /) -> None:
    """Set a secret by name.

    In sandbox/SDK mode, sets the secret in the registry-owned context.
    In direct mode, uses the secrets_manager context variable system.
    """
    if flags.registry_client:
        ctx = _secrets_ctx.get().copy()
        ctx[name] = value
        _secrets_ctx.set(ctx)
        return
    # Import lazily to avoid heavy tracecat imports in sandbox mode
    from tracecat.secrets import secrets_manager

    secrets_manager.set(name, value)
