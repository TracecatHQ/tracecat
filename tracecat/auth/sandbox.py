"""Tracecat authn sandbox."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator, Sequence
from types import TracebackType
from typing import Any, Literal, Self

from tracecat.contexts import ctx_role
from tracecat.db.schemas import Secret
from tracecat.logging import logger
from tracecat.secrets.constants import DEFAULT_SECRETS_ENVIRONMENT
from tracecat.secrets.encryption import decrypt_keyvalues
from tracecat.secrets.models import SearchSecretsParams, SecretKeyValue
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
        secrets: list[str] | None = None,
        target: Literal["env", "context"] = "env",
        environment: str = DEFAULT_SECRETS_ENVIRONMENT,
    ):
        self._role = role or ctx_role.get()
        self._secret_paths: list[str] = secrets or []
        self._secret_objs: Sequence[Secret] = []
        self._target = target
        self._context: dict[str, Any] = {}
        self._environment = environment
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
        for secret in self._secret_objs:
            keyvalues = decrypt_keyvalues(
                secret.encrypted_keys, key=self._encryption_key
            )
            for kv in keyvalues:
                yield secret.name, kv

    def _set_secrets(self) -> None:
        """Set secrets in the target."""
        if self._target == "context":
            logger.info(
                "Setting secrets in the context",
                paths=self._secret_paths,
                objs=self._secret_objs,
            )
            for name, kv in self._iter_secrets():
                if name not in self._context:
                    self._context[name] = {}
                self._context[name][kv.key] = kv.value.get_secret_value()
        else:
            logger.info("Setting secrets in the environment", paths=self._secret_paths)
            for _, kv in self._iter_secrets():
                os.environ[kv.key] = kv.value.get_secret_value()

    def _unset_secrets(self) -> None:
        if self._target == "context":
            for secret in self._secret_objs:
                if secret.name in self._context:
                    del self._context[secret.name]
        else:
            for _, kv in self._iter_secrets():
                if kv.key in os.environ:
                    del os.environ[kv.key]

    async def _get_secrets(self) -> Sequence[Secret]:
        """Retrieve secrets from a secrets manager."""
        return await self._get_secrets_from_service()

    async def _get_secrets_from_service(self) -> Sequence[Secret]:
        """Retrieve secrets from the secrets service."""
        logger.debug(
            "Retrieving secrets directly from db",
            secret_names=self._secret_paths,
            role=self._role,
            environment=self._environment,
        )

        unique_secret_names = {path.split(".")[0] for path in self._secret_paths}
        async with SecretsService.with_session(role=self._role) as service:
            logger.info("Retrieving secrets", secret_names=unique_secret_names)

            secrets = await service.search_secrets(
                SearchSecretsParams(
                    names=list(unique_secret_names), environment=self._environment
                )
            )

        if len(unique_secret_names) != len(secrets):
            missing_secrets = unique_secret_names - {secret.name for secret in secrets}
            raise TracecatCredentialsError(
                "Failed to retrieve secrets. Please contact support for help.",
                detail=[
                    {"secret_name": name, "environment": self._environment}
                    for name in missing_secrets
                ],
            )

        return secrets
