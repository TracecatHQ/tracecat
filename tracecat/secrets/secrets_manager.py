"""Tracecat secrets management."""

import contextlib
from collections.abc import Iterator

from tracecat.contexts import ctx_env
from tracecat.logger import logger


def get(name: str, default: str | None = None, /) -> str:
    """Get a secret that was set in the current context."""
    _env = ctx_env.get()
    logger.info(f"Getting secret {name=}", env=_env)
    try:
        return _env[name]
    except KeyError:
        return default


def set(name: str, value: str, /) -> None:
    """Set a secret in the current context."""
    _env = ctx_env.get()
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
    logger.debug("Initial env", initial_env=initial_env)
    token = ctx_env.set(initial_env)
    try:
        yield
    finally:
        ctx_env.reset(token)  # Reset to the original environment
