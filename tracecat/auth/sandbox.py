"""Tracecat authn sandbox."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from types import TracebackType
from typing import Any, Self

from tracecat.auth.types import Role
from tracecat.contexts import ctx_role
from tracecat.exceptions import TracecatCredentialsError
from tracecat.logger import logger
from tracecat.secrets.backend import get_secrets_backend
from tracecat.secrets.constants import DEFAULT_SECRETS_ENVIRONMENT


class AuthSandbox:
    """Context manager to temporarily set secrets in the environment as env vars.

    Motivation
    ----------
    - This allows use of `os.environ` to access secrets in the environment in UDFs.
    - We wrap the execution of a UDF with this context manager to set secrets in the environment.

    Secret values are resolved through the configured secrets backend
    (`TRACECAT__SECRETS_BACKEND`): the database backend decrypts values stored
    in Postgres, external backends (e.g. Vault) fetch them at runtime.
    """

    def __init__(
        self,
        role: Role | None = None,
        # This can be either 'my_secret.KEY' or 'my_secret'
        # Keys that are passed here are fetched from the secrets backend
        secrets: Iterable[str] | None = None,
        environment: str = DEFAULT_SECRETS_ENVIRONMENT,
        # Keys specified here will tell the sandbox to ignore if they are missing
        optional_secrets: Iterable[str] | None = None,  # Base secret names only
    ):
        self._role = role or ctx_role.get()
        self._secret_paths = set(secrets or [])
        self._context: dict[str, Any] = {}
        self._environment = environment
        self._optional_secrets = set(optional_secrets or [])

    def __enter__(self) -> Self:
        if self._secret_paths:
            self._context = asyncio.run(self._get_secrets())
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
            self._context = await self._get_secrets()
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

    def _unset_secrets(self) -> None:
        logger.trace("Cleaning up secrets")
        self._context = {}

    async def _get_secrets(self) -> dict[str, dict[str, str]]:
        """Retrieve secret values from the configured secrets backend."""
        backend = get_secrets_backend()
        logger.debug(
            "Retrieving secrets from backend",
            secret_names=self._secret_paths,
            role=self._role,
            environment=self._environment,
        )

        # These are a combination of required and optional secrets
        unique_secret_names = {path.split(".")[0] for path in self._secret_paths}
        logger.info("Retrieving secrets", secret_names=unique_secret_names)

        secret_values = await backend.get_secret_values(
            unique_secret_names,
            self._environment,
            scope="workspace",
            role=self._role,
        )

        # Filter out optional secrets
        unique_req_secret_names = {
            secret_name
            for secret_name in unique_secret_names
            if secret_name not in self._optional_secrets
        }
        defined_req_secret_names = {
            secret_name
            for secret_name in secret_values
            if secret_name not in self._optional_secrets
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
                f"Missing workspace secrets: {', '.join(missing_secrets)}.",
                detail=[
                    {"secret_name": name, "environment": self._environment}
                    for name in missing_secrets
                ],
            )

        return secret_values
