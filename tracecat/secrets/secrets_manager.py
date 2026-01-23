"""Tracecat secrets management."""

from __future__ import annotations

import builtins
import contextlib
from collections.abc import AsyncIterator, Iterator
from typing import TYPE_CHECKING, Any, overload

from tracecat.auth.sandbox import AuthSandbox
from tracecat.contexts import ctx_env, ctx_run, get_env
from tracecat.exceptions import TracecatCredentialsError
from tracecat.integrations.enums import OAuthGrantType
from tracecat.integrations.schemas import ProviderKey
from tracecat.integrations.service import IntegrationService
from tracecat.logger import logger
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.secrets.constants import DEFAULT_SECRETS_ENVIRONMENT

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


def _infer_grant_type_from_token_name(token_name: str) -> OAuthGrantType | None:
    """Infer the OAuth grant type from the token name suffix.

    Token names follow the pattern: <PREFIX>_USER_TOKEN or <PREFIX>_SERVICE_TOKEN
    - USER_TOKEN -> authorization_code
    - SERVICE_TOKEN -> client_credentials
    """
    if token_name.endswith("_USER_TOKEN"):
        return OAuthGrantType.AUTHORIZATION_CODE
    elif token_name.endswith("_SERVICE_TOKEN"):
        return OAuthGrantType.CLIENT_CREDENTIALS
    return None


async def get_action_secrets(
    secret_exprs: builtins.set[str],
    action_secrets: builtins.set[RegistrySecretType],
) -> dict[str, Any]:
    # Handle secrets from the task args
    args_secrets = secret_exprs
    # Get oauth integrations from the action secrets
    args_oauth_secrets: builtins.set[str] = builtins.set()
    args_basic_secrets: builtins.set[str] = builtins.set()
    for secret in args_secrets:
        if "." in secret:
            name, _ = secret.split(".", 1)
            if name.endswith("_oauth"):
                args_oauth_secrets.add(secret)
                continue
        args_basic_secrets.add(secret)

    # Handle secrets from the action
    required_basic_secrets: builtins.set[str] = builtins.set()
    optional_basic_secrets: builtins.set[str] = builtins.set()
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

    # Parse OAuth secrets from expressions (e.g., "microsoft_teams_oauth.MICROSOFT_TEAMS_USER_TOKEN")
    # These are user-specified OAuth secrets in action args, not declared by the action itself
    args_oauth_token_names: dict[ProviderKey, str] = {}
    for expr in args_oauth_secrets:
        # expr format: "<provider_id>_oauth.<TOKEN_NAME>"
        name, token_name = expr.split(".", 1)
        provider_id = name.removesuffix("_oauth")
        grant_type = _infer_grant_type_from_token_name(token_name)
        if grant_type is None:
            logger.warning(
                "Could not infer grant type from token name, skipping",
                expr=expr,
                token_name=token_name,
            )
            continue
        key = ProviderKey(id=provider_id, grant_type=grant_type)
        args_oauth_token_names[key] = token_name

    # Get secrets to fetch
    all_basic_secrets = (
        required_basic_secrets | args_basic_secrets | optional_basic_secrets
    )
    logger.info(
        "Handling secrets",
        required_basic_secrets=required_basic_secrets,
        optional_basic_secrets=optional_basic_secrets,
        oauth_provider_ids=oauth_secrets,
        args_oauth_token_names=args_oauth_token_names,
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

    # Get oauth integrations (from both action-declared secrets and expression-based secrets)
    all_oauth_provider_keys = builtins.set(oauth_secrets.keys()) | builtins.set(
        args_oauth_token_names.keys()
    )
    if all_oauth_provider_keys:
        try:
            async with IntegrationService.with_session() as service:
                oauth_integrations = await service.list_integrations(
                    provider_keys=all_oauth_provider_keys
                )
                fetched_keys: builtins.set[ProviderKey] = builtins.set()
                for integration in oauth_integrations:
                    provider_key = ProviderKey(
                        id=integration.provider_id,
                        grant_type=integration.grant_type,
                    )
                    fetched_keys.add(provider_key)
                    await service.refresh_token_if_needed(integration)
                    try:
                        if access_token := await service.get_access_token(integration):
                            # SECRETS.<provider_id>_oauth.[<prefix>_[SERVICE|USER]_TOKEN]
                            # NOTE: We are overriding the provider_id key here assuming its unique
                            # <prefix> is the provider_id in uppercase.
                            provider_secrets = secrets.setdefault(
                                f"{integration.provider_id}_oauth", {}
                            )
                            # Determine token_name from either action-declared secrets or expression-based secrets
                            if provider_key in oauth_secrets:
                                token_name = oauth_secrets[provider_key].token_name
                            else:
                                token_name = args_oauth_token_names[provider_key]
                            provider_secrets[token_name] = (
                                access_token.get_secret_value()
                            )
                    except Exception as e:
                        logger.warning(
                            "Could not get oauth secret, skipping",
                            error=e,
                            integration=integration,
                        )
                # Only check for missing required secrets from action-declared secrets
                # Expression-based secrets are user-provided and we don't enforce requirements on them
                missing_action_keys = builtins.set(oauth_secrets.keys()) - fetched_keys
                if missing_action_keys:
                    missing_required = [
                        key
                        for key in missing_action_keys
                        if not oauth_secrets[key].optional
                    ]
                    optional_missing = builtins.set(missing_action_keys) - builtins.set(
                        missing_required
                    )
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
                # Log if expression-based OAuth secrets were not found
                missing_expr_keys = (
                    builtins.set(args_oauth_token_names.keys()) - fetched_keys
                )
                if missing_expr_keys:
                    logger.warning(
                        "OAuth integrations from expressions not found",
                        providers=[
                            {
                                "provider_id": key.id,
                                "grant_type": key.grant_type.value,
                            }
                            for key in missing_expr_keys
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

    OAuth secrets are flattened the same way as regular secrets - their keys
    (like MICROSOFT_TEAMS_USER_TOKEN) are extracted and made available as environment variables.
    """
    flattened_secrets: dict[str, str] = {}
    for name, keyvalues in secrets.items():
        # Both OAuth and regular secrets are flattened by extracting their key-value pairs
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
    # Load action from index + manifest (not RegistryAction table)
    async with RegistryActionsService.with_session() as svc:
        indexed_result = await svc.get_action_from_index(action_type)
        if indexed_result is None:
            raise ValueError(f"Action '{action_type}' not found in registry")

        # Aggregate secrets from manifest
        manifest = indexed_result.manifest
        action_secrets = builtins.set(
            RegistryActionsService.aggregate_secrets_from_manifest(
                manifest, action_type
            )
        )

    secrets = await get_action_secrets(
        secret_exprs=builtins.set(), action_secrets=action_secrets
    )
    flat_secrets = flatten_secrets(secrets)
    with env_sandbox(flat_secrets):
        yield secrets
