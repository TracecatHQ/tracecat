from __future__ import annotations

import asyncio
import os

from sqlmodel import Session, select

from tracecat.contexts import ctx_role
from tracecat.db.schemas import Secret
from tracecat.logging import logger
from tracecat.types.api import UpdateSecretParams
from tracecat.types.auth import Role

from .encryption import decrypt_keyvalues, encrypt_keyvalues
from .models import SecretKeyValue


class SecretsService:
    """Secrets manager service."""

    def __init__(self, session: Session, role: Role | None = None):
        self.role = role or ctx_role.get()
        self.session = session
        self._encryption_key = os.getenv("TRACECAT__DB_ENCRYPTION_KEY")
        if not self._encryption_key:
            raise ValueError("TRACECAT__DB_ENCRYPTION_KEY is not set")
        self.logger = logger.bind(service="secrets")

    def list_secrets(self) -> list[Secret]:
        statement = select(Secret).where(Secret.owner_id == self.role.user_id)
        return self.session.exec(statement).all()

    def get_secret_by_id(self, secret_id: int) -> Secret | None:
        statement = select(Secret).where(
            Secret.owner_id == self.role.user_id, Secret.id == secret_id
        )
        return self.session.exec(statement).one_or_none()

    def get_secret_by_name(self, secret_name: str) -> Secret | None:
        statement = select(Secret).where(
            Secret.owner_id == self.role.user_id, Secret.name == secret_name
        )
        return self.session.exec(statement).one_or_none()

    async def aget_secret_by_name(self, secret_name: str) -> Secret | None:
        return await asyncio.to_thread(self.get_secret_by_name, secret_name)

    def create_secret(self, secret: Secret) -> Secret:
        """Create a new secret."""
        self.session.add(secret)
        self.session.commit()
        self.session.refresh(secret)
        return secret

    def update_secret(self, secret: Secret, params: UpdateSecretParams) -> Secret:
        """Update a secret."""
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
        self.session.commit()
        self.session.refresh(secret)
        return secret

    def delete_secret(self, secret: Secret) -> None:
        """Delete a secret by name."""
        self.session.delete(secret)
        self.session.commit()

    def decrypt_keys(self, encrypted_keys: bytes) -> list[SecretKeyValue]:
        """Decrypt and return the keys for a secret."""
        return decrypt_keyvalues(encrypted_keys, key=self._encryption_key)

    def encrypt_keys(self, keys: list[SecretKeyValue]) -> bytes:
        """Encrypt and return the keys for a secret."""
        return encrypt_keyvalues(keys, key=self._encryption_key)
