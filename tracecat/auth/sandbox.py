"""Tracecat authn sandbox."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator, Sequence
from types import TracebackType
from typing import Any, Literal, Self

from tracecat.contexts import ctx_role
from tracecat.db.schemas import BaseSecret
from tracecat.logger import logger
from tracecat.secrets.constants import DEFAULT_SECRETS_ENVIRONMENT
from tracecat.secrets.encryption import decrypt_keyvalues
from tracecat.secrets.models import SecretKeyValue, SecretSearch
from tracecat.secrets.service import SecretsService
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatCredentialsError


class AuthSandbox:
    """Context manager to temporarily set secrets in the environment as env vars.

    Motivation
    ----------
    - This allows use of `os.environ` to access secrets in the environment in UDFs.
    - We wrap the execution of a UDF with this context manager to set secrets in the environment.
    """

    def __init__(
        self,
        role: Role | None = None,
        # This can be either 'my_secret.KEY' or 'my_secret'
        secrets: list[str] | None = None,
        environment: str = DEFAULT_SECRETS_ENVIRONMENT,
        optional_secrets: list[str] | None = None,  # Base secret names only
    ):
        self._role = role or ctx_role.get()
        self._secret_paths: list[str] = secrets or []
        self._secret_objs: Sequence[BaseSecret] = []
        self._context: dict[str, Any] = {}
        self._environment = environment
        self._optional_secrets = set(optional_secrets or [])
        try:
            self._encryption_key = os.environ["TRACECAT__DB_ENCRYPTION_KEY"]
        except KeyError as e:
            raise KeyError("TRACECAT__DB_ENCRYPTION_KEY is not set") from e

    def __enter__(self) -> Self:
        if self._secret_paths:
            self._secret_objs = asyncio.run(self._get_secrets())
            self._set_secrets()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException],
        exc_value: BaseException,
        traceback: TracebackType,
    ) -> None:
        self._unset_secrets()
        if exc_type is not None:
            logger.error(
                "An error occurred inside AuthSandbox. If you are seeing this, please contact support.",
                exc_info=(exc_type, exc_value, traceback),
            )

    async def __aenter__(self) -> Self:
        if self._secret_paths:
            self._secret_objs = await self._get_secrets()
            self._set_secrets()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException],
        exc_value: BaseException,
        traceback: TracebackType,
    ) -> None:
        return self.__exit__(exc_type, exc_value, traceback)

    @property
    def secrets(self) -> dict[str, Any]:
        """Return secret names mapped to their secret key value pairs."""
        return self._context

    def _iter_secrets(self) -> Iterator[tuple[str, SecretKeyValue]]:
        """Iterate over the secrets."""
        try:
            for secret in self._secret_objs:
                keyvalues = decrypt_keyvalues(
                    secret.encrypted_keys, key=self._encryption_key
                )
                for kv in keyvalues:
                    yield secret.name, kv
        except Exception as e:
            logger.error(f"Error decrypting secrets: {e!r}")
            raise

    def _set_secrets(self) -> None:
        """Set secrets in the target."""
        logger.info(
            "Setting secrets",
            paths=self._secret_paths,
            objs=self._secret_objs,
        )
        for name, kv in self._iter_secrets():
            if name not in self._context:
                self._context[name] = {}
            self._context[name][kv.key] = kv.value.get_secret_value()

    def _unset_secrets(self) -> None:
        logger.trace("Cleaning up secrets")
        for secret in self._secret_objs:
            if secret.name in self._context:
                del self._context[secret.name]

    async def _get_secrets(self) -> Sequence[BaseSecret]:
        """Retrieve secrets from a secrets manager."""
        return await self._get_secrets_from_service()

    async def _get_secrets_from_service(self) -> Sequence[BaseSecret]:
        """Retrieve secrets from the secrets service."""
        logger.debug(
            "Retrieving secrets directly from db",
            secret_names=self._secret_paths,
            role=self._role,
            environment=self._environment,
        )

        # These are a combination of required and optional secrets
        unique_secret_names = {path.split(".")[0] for path in self._secret_paths}
        async with SecretsService.with_session(role=self._role) as service:
            logger.info("Retrieving secrets", secret_names=unique_secret_names)

            secrets = await service.search_secrets(
                SecretSearch(names=unique_secret_names, environment=self._environment)
            )

        # Filter out optional secrets
        unique_req_secret_names = {
            secret_name
            for secret_name in unique_secret_names
            if secret_name not in self._optional_secrets
        }
        defined_req_secret_names = {
            secret.name
            for secret in secrets
            if secret.name not in self._optional_secrets
        }
        logger.debug(
            "Retrieved secrets",
            required_secrets=defined_req_secret_names,
            optional_secrets=self._optional_secrets,
        )

        # This check only validates required/optional secrets and doesn't validate secret keys
        if len(unique_req_secret_names) != len(defined_req_secret_names):
            missing_secrets = unique_req_secret_names - defined_req_secret_names
            logger.error("Missing secrets", missing_secrets=missing_secrets)
            raise TracecatCredentialsError(
                f"Missing secrets: {', '.join(missing_secrets)}",
                detail=[
                    {"secret_name": name, "environment": self._environment}
                    for name in missing_secrets
                ],
            )

        return secrets
