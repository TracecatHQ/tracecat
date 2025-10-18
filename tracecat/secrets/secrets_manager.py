"""Tracecat secrets management."""

from __future__ import annotations

import builtins
import contextlib
from collections.abc import Iterator
from typing import overload

from tracecat.contexts import ctx_env, get_env
from tracecat.integrations.enums import OAuthGrantType
from tracecat.integrations.models import ProviderKey
from tracecat.integrations.service import IntegrationService
from tracecat.logger import logger
from tracecat.types.exceptions import TracecatCredentialsError


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


async def get_oauth_context(
    oauth_expressions: builtins.set[str],
) -> dict[str, dict[str, str]]:
    """Build OAuth context for execution environments."""

    if not oauth_expressions:
        return {}

    provider_keys: builtins.set[ProviderKey] = builtins.set()
    expression_map: dict[ProviderKey, str] = {}

    for expr in oauth_expressions:
        parts = expr.split(".")
        if len(parts) != 2:
            logger.warning("Invalid OAUTH expression format", expression=expr)
            continue
        provider_id, token_type = parts
        match token_type:
            case "SERVICE_TOKEN":
                grant_type = OAuthGrantType.CLIENT_CREDENTIALS
            case "USER_TOKEN":
                grant_type = OAuthGrantType.AUTHORIZATION_CODE
            case _:
                logger.warning(
                    "Unsupported OAUTH token type",
                    token_type=token_type,
                    expression=expr,
                )
                continue
        key = ProviderKey(id=provider_id, grant_type=grant_type)
        provider_keys.add(key)
        expression_map[key] = expr

    if not provider_keys:
        return {}

    oauth_context: dict[str, dict[str, str]] = {}
    fetched_keys: builtins.set[ProviderKey] = builtins.set()

    async with IntegrationService.with_session() as service:
        integrations = await service.list_integrations(provider_keys=provider_keys)
        for integration in integrations:
            provider_key = ProviderKey(
                id=integration.provider_id, grant_type=integration.grant_type
            )
            fetched_keys.add(provider_key)

            try:
                await service.refresh_token_if_needed(integration)
            except Exception as e:  # pragma: no cover - defensive logging
                logger.warning(
                    "Failed to refresh OAuth token",
                    provider_id=integration.provider_id,
                    grant_type=integration.grant_type.value,
                    error=e,
                )
                continue

            try:
                access_token = await service.get_access_token(integration)
            except Exception as e:  # pragma: no cover - defensive logging
                logger.warning(
                    "Failed to retrieve OAuth access token",
                    provider_id=integration.provider_id,
                    grant_type=integration.grant_type.value,
                    error=e,
                )
                continue

            if access_token is None:
                logger.warning(
                    "OAuth integration returned no access token",
                    provider_id=integration.provider_id,
                    grant_type=integration.grant_type.value,
                )
                continue

            token_type = (
                "SERVICE_TOKEN"
                if integration.grant_type == OAuthGrantType.CLIENT_CREDENTIALS
                else "USER_TOKEN"
            )
            provider_dict = oauth_context.setdefault(integration.provider_id, {})
            provider_dict[token_type] = access_token.get_secret_value()

    missing = provider_keys - fetched_keys
    if missing:
        missing_detail = [
            {"provider_id": key.id, "grant_type": key.grant_type.value}
            for key in missing
        ]
        for key in missing:
            logger.warning(
                "OAuth integration not configured",
                provider_id=key.id,
                grant_type=key.grant_type.value,
                expression=expression_map.get(key),
            )
        raise TracecatCredentialsError(
            "Missing required OAuth integrations for OAUTH expressions",
            detail=missing_detail,
        )

    return oauth_context
