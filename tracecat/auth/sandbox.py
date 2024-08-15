"""Tracecat authn sandbox."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from typing import Literal, Self

from httpx import HTTPStatusError
from loguru import logger

from tracecat.clients import AuthenticatedAPIClient
from tracecat.concurrency import GatheringTaskGroup
from tracecat.contexts import ctx_role
from tracecat.db.schemas import Secret
from tracecat.secrets.encryption import decrypt_keyvalues
from tracecat.secrets.models import SecretKeyValue
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
    ):
        self._role = role or ctx_role.get()
        self._secret_paths: list[str] = secrets or []
        self._secret_objs: list[Secret] = []
        self._target = target
        self._context = {}
        self._encryption_key = os.getenv("TRACECAT__DB_ENCRYPTION_KEY")
        if not self._encryption_key:
            raise ValueError("TRACECAT__DB_ENCRYPTION_KEY is not set")

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

    def _iter_secrets(self) -> Iterator[tuple[str, SecretKeyValue]]:
        """Iterate over the secrets."""
        for secret in self._secret_objs:
            keyvalues = decrypt_keyvalues(
                secret.encrypted_keys, key=self._encryption_key
            )
            for kv in keyvalues:
                yield secret.name, kv

    def _set_secrets(self):
        """Set secrets in the target."""
        if self._target == "context":
            logger.info(
                "Setting secrets in the context",
                paths=self._secret_paths,
                objs=self._secret_objs,
            )
            for name, kv in self._iter_secrets():
                self._context[name] = {kv.key: kv.value.get_secret_value()}
        else:
            logger.info("Setting secrets in the environment", paths=self._secret_paths)
            for _, kv in self._iter_secrets():
                os.environ[kv.key] = kv.value.get_secret_value()

    def _unset_secrets(self):
        if self._target == "context":
            for secret in self._secret_objs:
                if secret.name in self._context:
                    del self._context[secret.name]
        else:
            for _, kv in self._iter_secrets():
                if kv.key in os.environ:
                    del os.environ[kv.key]

    async def _get_secrets(self) -> list[Secret]:
        """Retrieve secrets from the secrets API."""

        logger.info(
            "Retrieving secrets from the secrets API",
            secret_names=self._secret_paths,
            role=self._role,
        )
        secret_names = (path.split(".")[0] for path in self._secret_paths)

        try:
            async with (
                AuthenticatedAPIClient(
                    role=self._role, params={"workspace_id": self._role.workspace_id}
                ) as client,
                GatheringTaskGroup() as tg,
            ):
                for secret_name in secret_names:

                    async def fetcher(name: str):
                        try:
                            res = await client.get(f"/secrets/{name}")
                            res.raise_for_status()  # Raise an exception for HTTP error codes
                            return res
                        except HTTPStatusError as e:
                            msg = (
                                f"Failed to retrieve secret {name!r}."
                                f" Please ensure you have set all required secrets: {self._secret_paths}"
                            )
                            detail = e.response.text
                            logger.error(msg, detail=detail)
                            raise TracecatCredentialsError(msg, detail=detail) from e
                        except Exception as e:
                            msg = f"Failed to retrieve secret {name!r}."
                            detail = str(e)
                            logger.error(msg, detail=detail)
                            raise TracecatCredentialsError(msg, detail=detail) from e

                    tg.create_task(fetcher(secret_name))
        except* TracecatCredentialsError as eg:
            raise TracecatCredentialsError(
                "Failed to retrieve secrets",
                detail={
                    "errors": [
                        str(x)
                        for x in eg.exceptions
                        if isinstance(x, TracecatCredentialsError)
                    ],
                    "secrets": self._secret_paths,
                },
            ) from eg

        return [
            Secret.model_validate_json(secret_bytes.content)
            for secret_bytes in tg.results()
        ]
