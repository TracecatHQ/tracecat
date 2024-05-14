from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING, Self

from tracecat.auth import AuthenticatedAPIClient
from tracecat.db import Secret
from tracecat.logging import logger

if TYPE_CHECKING:
    from tracecat.auth import Role


class AuthSandbox:
    """Context manager to temporarily set secrets in the environment."""

    _role: Role
    _secret_names: list[str]
    _secret_objs: list[Secret]

    def __init__(self, role: Role, secrets: list[str] | None = None):
        self._role = role
        self._secret_names = secrets
        self._secret_objs = []

    def __enter__(self) -> Self:
        if self._secret_names:
            self.secret_objs = asyncio.run(self._get_secrets())
            self._set_secrets()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self._unset_secrets()
        if exc_type is not None:
            logger.error("An error occurred", exc_info=(exc_type, exc_value, traceback))

    async def __aenter__(self):
        if self._secret_names:
            self.secret_objs = await self._get_secrets()
            self._set_secrets()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        return self.__exit__(exc_type, exc_value, traceback)

    def _set_secrets(self):
        """Set secrets in the environment."""
        for secret in self._secret_objs:
            logger.info("Setting secret {!r}", secret.name)
            for kv in secret.keys:
                os.environ[kv.key] = kv.value

    def _unset_secrets(self):
        for secret in self._secret_objs:
            logger.info("Deleting secret {!r}", secret.name)
            for kv in secret.keys:
                del os.environ[kv.key]

    async def _get_secrets(self) -> list[Secret]:
        """Retrieve secrets from the secrets API."""
        async with AuthenticatedAPIClient(role=self._role) as client:
            # NOTE(perf): This is not really batched - room for improvement
            secret_responses = await asyncio.gather(
                *[
                    client.get(f"/secrets/{secret_name}")
                    for secret_name in self._secret_names
                ]
            )
            return [
                Secret.model_validate_json(secret_bytes.content)
                for secret_bytes in secret_responses
            ]
