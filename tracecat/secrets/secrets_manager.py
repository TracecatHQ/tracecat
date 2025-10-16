"""Tracecat secrets management."""

from __future__ import annotations

import contextlib
from builtins import set as Set  # Avoid clashing with set() function
from collections.abc import AsyncIterator, Iterator, Mapping
from typing import TYPE_CHECKING, Any, overload

from tracecat.auth.sandbox import AuthSandbox
from tracecat.contexts import ctx_env, ctx_run, get_env
from tracecat.expressions.eval import extract_templated_secrets
from tracecat.integrations.enums import OAuthGrantType
from tracecat.integrations.models import ProviderKey
from tracecat.integrations.service import IntegrationService
from tracecat.logger import logger
from tracecat.secrets.constants import DEFAULT_SECRETS_ENVIRONMENT
from tracecat.types.exceptions import TracecatCredentialsError

if TYPE_CHECKING:
    from tracecat_registry import RegistrySecretType
    from tracecat_registry._internal.models import RegistryOAuthSecret


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


def get_runtime_env() -> str:
    """Get the runtime environment from `ctx_run` contextvar. Defaults to `default` if not set."""
    return getattr(ctx_run.get(), "environment", DEFAULT_SECRETS_ENVIRONMENT)


async def get_action_secrets(
    args: Mapping[str, Any],
    action_secrets: Set[RegistrySecretType],
) -> dict[str, Any]:
    # Handle secrets from the task args
    args_secrets = extract_templated_secrets(args)
    # Get oauth integrations from the action secrets
    args_oauth_secrets: Set[str] = Set()
    args_basic_secrets: Set[str] = Set()
    for secret in args_secrets:
        if secret.endswith("_oauth"):
            args_oauth_secrets.add(secret)
        else:
            args_basic_secrets.add(secret)

    # Handle secrets from the action
    required_basic_secrets: Set[str] = Set()
    optional_basic_secrets: Set[str] = Set()
    oauth_secrets: dict[ProviderKey, RegistryOAuthSecret] = {}
    for secret in action_secrets:
        if secret.type == "oauth":
            key = ProviderKey(
                id=secret.provider_id, grant_type=OAuthGrantType(secret.grant_type)
            )
            oauth_secrets[key] = secret
        elif secret.optional:
            optional_basic_secrets.add(secret.name)
        else:
            required_basic_secrets.add(secret.name)

    # Get secrets to fetch
    all_basic_secrets = (
        required_basic_secrets | args_basic_secrets | optional_basic_secrets
    )
    logger.info(
        "Handling secrets",
        required_basic_secrets=required_basic_secrets,
        optional_basic_secrets=optional_basic_secrets,
        oauth_provider_ids=oauth_secrets,
        args_secrets=args_secrets,
        secrets_to_fetch=all_basic_secrets,
    )

    # Get all basic secrets in one call
    secrets: dict[str, Any] = {}
    async with AuthSandbox(
        secrets=all_basic_secrets,
        environment=get_runtime_env(),
        optional_secrets=optional_basic_secrets,
    ) as sandbox:
        secrets |= sandbox.secrets.copy()

    # Get oauth integrations
    if oauth_secrets:
        try:
            async with IntegrationService.with_session() as service:
                oauth_integrations = await service.list_integrations(
                    provider_keys=Set(oauth_secrets.keys())
                )
                fetched_keys: Set[ProviderKey] = Set()
                for integration in oauth_integrations:
                    provider_key = ProviderKey(
                        id=integration.provider_id,
                        grant_type=integration.grant_type,
                    )
                    fetched_keys.add(provider_key)
                    await service.refresh_token_if_needed(integration)
                    try:
                        if access_token := await service.get_access_token(integration):
                            secret = oauth_secrets[provider_key]
                            # SECRETS.<provider_id>.[<prefix>_[SERVICE|USER]_TOKEN]
                            # NOTE: We are overriding the provider_id key here assuming its unique
                            # <prefix> is the provider_id in uppercase.
                            provider_secrets = secrets.setdefault(
                                integration.provider_id, {}
                            )
                            provider_secrets[secret.token_name] = (
                                access_token.get_secret_value()
                            )
                    except Exception as e:
                        logger.warning(
                            "Could not get oauth secret, skipping",
                            error=e,
                            integration=integration,
                        )
                missing_keys = Set(oauth_secrets.keys()) - fetched_keys
                if missing_keys:
                    missing_required = [
                        key for key in missing_keys if not oauth_secrets[key].optional
                    ]
                    optional_missing = Set(missing_keys) - Set(missing_required)
                    if optional_missing:
                        logger.info(
                            "Optional OAuth integrations not configured",
                            providers=[
                                {
                                    "provider_id": key.id,
                                    "grant_type": key.grant_type.value,
                                }
                                for key in optional_missing
                            ],
                        )
                    if missing_required:
                        raise TracecatCredentialsError(
                            "Missing required OAuth integrations",
                            detail=[
                                {
                                    "provider_id": key.id,
                                    "grant_type": key.grant_type.value,
                                }
                                for key in missing_required
                            ],
                        )
        except TracecatCredentialsError:
            raise
        except Exception as e:
            logger.warning("Could not get oauth secrets", error=e)
    return secrets


def flatten_secrets(secrets: dict[str, dict[str, str]]) -> dict[str, str]:
    """Given secrets in the format of {name: {key: value}}, we need to flatten
    it to a dict[str, str] to set in the environment context.

    For example, if you have the secret `my_secret.KEY`, then you access this in the UDF
    as `KEY`. This means you cannot have a clashing key in different secrets.

    OAuth secrets are handled differently - they're stored as direct string values
    and are accessible as environment variables using their provider_id.
    """
    flattened_secrets: dict[str, str] = {}
    for name, keyvalues in secrets.items():
        if name.endswith("_oauth"):
            # OAuth secrets are stored as direct string values
            flattened_secrets[name] = str(keyvalues)
        else:
            # Regular secrets are stored as key-value dictionaries
            for key, value in keyvalues.items():
                if key in flattened_secrets:
                    raise ValueError(
                        f"Key {key!r} is duplicated in {name!r}! "
                        "Please ensure only one secret with a given name is set. "
                        "e.g. If you have `first_secret.KEY` set, then you cannot "
                        "also set `second_secret.KEY` as `KEY` is duplicated."
                    )
                flattened_secrets[key] = value
    return flattened_secrets


@contextlib.asynccontextmanager
async def load_secrets(action_type: str) -> AsyncIterator[dict[str, Any]]:
    from tracecat.registry.actions.service import RegistryActionsService

    async with RegistryActionsService.with_session() as svc:
        reg_action = await svc.get_action(action_type)
        action_secrets = await svc.fetch_all_action_secrets(reg_action)

    secrets = await get_action_secrets(args={}, action_secrets=action_secrets)
    flat_secrets = flatten_secrets(secrets)
    with env_sandbox(flat_secrets):
        yield secrets
