"""Tracecat authn sandbox."""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING, Literal, Self

from loguru import logger

from tracecat.auth.clients import AuthenticatedAPIClient
from tracecat.auth.credentials import Role
from tracecat.contexts import ctx_role

if TYPE_CHECKING:
    from tracecat.db.schemas import Secret


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
    ):
        self._role = role or ctx_role.get()
        self._secret_paths: list[str] = secrets
        self._secret_objs: list[Secret] = []
        self._target = target
        self._context = {}

    def __enter__(self) -> Self:
        if self._secret_paths:
            self._secret_objs = asyncio.run(self._get_secrets())
            self._set_secrets()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self._unset_secrets()
        if exc_type is not None:
            logger.error("An error occurred", exc_info=(exc_type, exc_value, traceback))

    async def __aenter__(self):
        if self._secret_paths:
            self._secret_objs = await self._get_secrets()
            self._set_secrets()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        return self.__exit__(exc_type, exc_value, traceback)

    @property
    def secrets(self) -> dict[str, str]:
        """Return secret names mapped to their secret key value pairs."""
        return self._context

    def _set_secrets(self):
        """Set secrets in the target."""
        if self._target == "context":
            logger.info(
                "Setting secrets in the context",
                paths=self._secret_paths,
                objs=self._secret_objs,
            )
            for secret in self._secret_objs:
                self._context[secret.name] = {kv.key: kv.value for kv in secret.keys}
        else:
            logger.info("Setting secrets in the environment", paths=self._secret_paths)
            for secret in self._secret_objs:
                for kv in secret.keys:
                    os.environ[kv.key] = kv.value

    def _unset_secrets(self):
        if self._target == "context":
            for secret in self._secret_objs:
                del self._context[secret.name]
        else:
            for secret in self._secret_objs:
                for kv in secret.keys:
                    del os.environ[kv.key]

    async def _get_secrets(self) -> list[Secret]:
        """Retrieve secrets from the secrets API."""

        # XXX: This import is necessary to avoid horrendous circular import errors
        from tracecat.db.schemas import Secret

        logger.info(
            "Retrieving secrets from the secrets API",
            secret_names=self._secret_paths,
            role=self._role,
        )
        secret_names = (path.split(".")[0] for path in self._secret_paths)

        async with AuthenticatedAPIClient(role=self._role) as client:
            # NOTE(perf): This is not really batched - room for improvement
            secret_responses = await asyncio.gather(
                *[client.get(f"/secrets/{secret_name}") for secret_name in secret_names]
            )
            return [
                Secret.model_validate_json(secret_bytes.content)
                for secret_bytes in secret_responses
            ]
