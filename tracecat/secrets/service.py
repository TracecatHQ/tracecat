from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Literal, overload

from sqlalchemy.exc import NoResultFound
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.contexts import ctx_role
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.schemas import Secret
from tracecat.logging import logger
from tracecat.secrets.encryption import decrypt_keyvalues, encrypt_keyvalues
from tracecat.secrets.models import (
    CreateSecretParams,
    SecretKeyValue,
    UpdateSecretParams,
)
from tracecat.types.auth import Role


class SecretsService:
    """Secrets manager service."""

    def __init__(self, session: AsyncSession, role: Role | None = None):
        self.role = role or ctx_role.get()
        self.session = session
        self._encryption_key = os.getenv("TRACECAT__DB_ENCRYPTION_KEY")
        if not self._encryption_key:
            raise ValueError("TRACECAT__DB_ENCRYPTION_KEY is not set")
        self.logger = logger.bind(service="secrets")

    @asynccontextmanager
    @staticmethod
    async def with_session(
        role: Role | None = None,
    ) -> AsyncGenerator[SecretsService, None, None]:
        async with get_async_session_context_manager() as session:
            yield SecretsService(session, role=role)

    async def _update_secret(self, secret: Secret, params: UpdateSecretParams):
        set_fields = params.model_dump(exclude_unset=True)
        # Handle keys separately
        if keys := set_fields.pop("keys", None):
            keyvalues = [SecretKeyValue(**kv) for kv in keys]
            secret.encrypted_keys = encrypt_keyvalues(
                keyvalues, key=self._encryption_key
            )

        for field, value in set_fields.items():
            setattr(secret, field, value)
        self.session.add(secret)
        await self.session.commit()

    async def _delete_secret(self, secret: Secret) -> None:
        """Delete a secret by name."""
        await self.session.delete(secret)
        await self.session.commit()

    async def list_secrets(self) -> list[Secret]:
        statement = select(Secret).where(Secret.owner_id == self.role.workspace_id)
        result = await self.session.exec(statement)
        return result.all()

    @overload
    async def get_secret_by_id(
        self, secret_id: str, raise_on_none: Literal[True]
    ) -> Secret: ...

    @overload
    async def get_secret_by_id(
        self, secret_id: str, raise_on_none: Literal[False]
    ) -> Secret | None: ...

    async def get_secret_by_id(
        self, secret_id: str, raise_on_none: bool = False
    ) -> Secret | None:
        statement = select(Secret).where(
            Secret.owner_id == self.role.workspace_id, Secret.id == secret_id
        )
        result = await self.session.exec(statement)
        secret = result.one_or_none()
        if not secret and raise_on_none:
            raise NoResultFound("Secret not found when searching by ID")
        return secret

    @overload
    async def get_secret_by_name(
        self, secret_name: str, raise_on_none: Literal[True]
    ) -> Secret: ...

    @overload
    async def get_secret_by_name(
        self, secret_name: str, raise_on_none: Literal[False]
    ) -> Secret | None: ...

    async def get_secret_by_name(
        self, secret_name: str, raise_on_none: bool = False
    ) -> Secret | None:
        statement = select(Secret).where(
            Secret.owner_id == self.role.workspace_id, Secret.name == secret_name
        )
        result = await self.session.exec(statement)
        secret = result.one_or_none()
        if not secret and raise_on_none:
            raise NoResultFound("Secret not found when searching by name")
        return secret

    async def create_secret(self, params: CreateSecretParams) -> None:
        secret = Secret(
            owner_id=self.role.workspace_id,
            name=params.name,
            type=params.type,
            description=params.description,
            tags=params.tags,
            encrypted_keys=self.encrypt_keys(params.keys),
        )
        self.session.add(secret)
        await self.session.commit()

    async def update_secret_by_name(
        self, secret_name: str, params: UpdateSecretParams
    ) -> None:
        secret = await self.get_secret_by_name(secret_name, raise_on_none=True)
        await self._update_secret(secret=secret, params=params)

    async def update_secret_by_id(
        self, secret_id: str, params: UpdateSecretParams
    ) -> None:
        secret = await self.get_secret_by_name(secret_id, raise_on_none=True)
        await self._update_secret(secret=secret, params=params)

    async def delete_secret_by_name(self, secret_name: str) -> None:
        secret = await self.get_secret_by_name(secret_name, raise_on_none=True)
        await self._delete_secret(secret)

    async def delete_secret_by_id(self, secret_id: str) -> None:
        secret = await self.get_secret_by_id(secret_id, raise_on_none=True)
        await self._delete_secret(secret)

    def decrypt_keys(self, encrypted_keys: bytes) -> list[SecretKeyValue]:
        """Decrypt and return the keys for a secret."""
        return decrypt_keyvalues(encrypted_keys, key=self._encryption_key)

    def encrypt_keys(self, keys: list[SecretKeyValue]) -> bytes:
        """Encrypt and return the keys for a secret."""
        return encrypt_keyvalues(keys, key=self._encryption_key)
