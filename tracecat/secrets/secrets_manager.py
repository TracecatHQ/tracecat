"""Tracecat secrets management."""

import contextlib
import os
from collections.abc import Iterator
from typing import overload

from tracecat.contexts import ctx_env, get_env


@overload
def get(name: str, /) -> str | None: ...


@overload
def get[T](name: str, default: T, /) -> str | T: ...


def get[T](name: str, default: T | None = None, /) -> str | T | None:
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


@contextlib.contextmanager
def use_agent_credentials() -> Iterator[None]:
    """Create a sandboxed environment for executing code with isolated environment variables."""
    with env_sandbox(
        {
            "AWS_ACCESS_KEY_ID": os.environ["AWS_ACCESS_KEY_ID"],
            "AWS_SECRET_ACCESS_KEY": os.environ["AWS_SECRET_ACCESS_KEY"],
            "AWS_REGION": os.environ["AWS_REGION"],
        }
    ):
        yield
