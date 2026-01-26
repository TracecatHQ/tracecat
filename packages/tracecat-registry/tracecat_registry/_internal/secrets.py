import contextvars
import os
from typing import overload

from tracecat_registry._internal.exceptions import SecretNotFoundError

# Registry-owned secrets context
_secrets_ctx: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar(
    "registry_secrets_ctx", default={}
)

# Type alias for the token returned by set_context
SecretsToken = contextvars.Token[dict[str, str]]


def init_from_env(keys: list[str] | None = None) -> SecretsToken:
    """Initialize secrets context from environment variables.

    Called by executor before running UDF in sandbox mode.
    If keys is None, copies all environment variables.
    Otherwise, only copies the specified keys.

    Returns a token that can be used to reset the context.
    """
    if keys is None:
        return _secrets_ctx.set(dict(os.environ))
    else:
        return _secrets_ctx.set({k: os.environ[k] for k in keys if k in os.environ})


def set_context(secrets: dict[str, str]) -> SecretsToken:
    """Set the secrets context directly.

    Called by executor to initialize registry secrets before running UDFs.
    Returns a token that can be used to reset the context via reset_context().
    """
    return _secrets_ctx.set(secrets)


def reset_context(token: SecretsToken) -> None:
    """Reset the secrets context to its previous state.

    Args:
        token: The token returned by set_context() or init_from_env().
    """
    _secrets_ctx.reset(token)


# Overload declarations for type checking
@overload
def get_or_default(name: str, /) -> str | None: ...


@overload
def get_or_default[T](name: str, default: T, /) -> str | T: ...


def get_or_default[T](name: str, default: T | None = None, /) -> str | T | None:
    """Lookup a secret by name, or return a default value if not found.

    Reads from the registry-owned secrets context.
    """
    return _secrets_ctx.get().get(name, default)


def get(name: str) -> str:
    """Lookup a secret by name, or raise an error if not found.

    Reads from the registry-owned secrets context.
    """
    ctx = _secrets_ctx.get()
    if name in ctx:
        return ctx[name]
    raise SecretNotFoundError(f"Secret '{name}' is required but not found.")


def set(name: str, value: str, /) -> None:
    """Set a secret by name.

    Sets the secret in the registry-owned context.
    """
    ctx = _secrets_ctx.get().copy()
    ctx[name] = value
    _secrets_ctx.set(ctx)
