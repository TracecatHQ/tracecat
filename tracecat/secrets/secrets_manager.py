"""Tracecat secrets management."""

import contextlib
from collections.abc import Iterator
from typing import overload

from tracecat.contexts import ctx_env, get_env


@overload
def get(name: str, default: None = None, /) -> str | None: ...


@overload
def get(name: str, default: str, /) -> str: ...


def get(name: str, default: str | None = None, /) -> str | None:
    """Get a secret that was set in the current context."""
    _env = get_env()
    return _env.get(name, default)


def set(name: str, value: str, /) -> None:
    """Set a secret in the current context."""
    _env = get_env()
    _env[name] = value
    ctx_env.set(_env)


@contextlib.contextmanager
def env_sandbox(
    initial_env: dict[str, str] | None = None,
) -> Iterator[None]:
    """
    Create a sandboxed environment for executing code with isolated environment variables.

    This context manager sets up an environment with initial secrets (if provided),
    yields control to the caller, and then resets the environment to its original state upon exit.

    Parameters
    ----------
    initial_secret_context : SecretContextEnv | None, optional
        Initial secrets to populate the environment with.

    Yields
    ------
    None

    Raises
    ------
    ValueError
        If there are duplicate keys in the initial_secret_context.

    Examples
    --------
    >>> with env_sandbox({"API_KEY": "abc123"}):
    ...     # Code executed here will have a special environment accessible
    ...     # through ctx_env.get()
    ...     api_key = get("API_KEY")
    ...     # Use api_key...
    """
    initial_env = initial_env or {}
    token = ctx_env.set(initial_env)
    try:
        yield
    finally:
        ctx_env.reset(token)  # Reset to the original environment
