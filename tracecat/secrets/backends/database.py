"""Database secrets backend: encrypted values stored in the Tracecat DB.

This is the default backend and preserves the pre-backend behavior exactly:
values are fetched via :class:`SecretsService` and decrypted with the Fernet
key from ``TRACECAT__DB_ENCRYPTION_KEY``.
"""

from __future__ import annotations

from collections.abc import Sequence

from tracecat.auth.secrets import get_db_encryption_key
from tracecat.auth.types import Role
from tracecat.db.models import BaseSecret
from tracecat.exceptions import TracecatNotFoundError
from tracecat.logger import logger
from tracecat.secrets.backend import SecretRegistration, SecretScope
from tracecat.secrets.encryption import decrypt_keyvalues
from tracecat.secrets.enums import SecretType
from tracecat.secrets.schemas import SecretSearch
from tracecat.secrets.service import SecretsService


class DatabaseSecretsBackend:
    """Resolve secret values from the Tracecat database."""

    @property
    def can_write(self) -> bool:
        return True

    async def get_secret_values(
        self,
        names: set[str],
        environment: str,
        *,
        scope: SecretScope = "workspace",
        role: Role | None = None,
    ) -> dict[str, dict[str, str]]:
        secrets = await self._fetch_secrets(
            names, environment=environment, scope=scope, role=role
        )
        encryption_key = get_db_encryption_key()
        values: dict[str, dict[str, str]] = {}
        try:
            for secret in secrets:
                keyvalues = decrypt_keyvalues(
                    secret.encrypted_keys, key=encryption_key
                )
                kv_map = values.setdefault(secret.name, {})
                for kv in keyvalues:
                    kv_map[kv.key] = kv.value.get_secret_value()
        except Exception as e:
            logger.error(f"Error decrypting secrets: {e!r}")
            raise
        return values

    async def list_registrations(
        self,
        environment: str | None = None,
        *,
        scope: SecretScope = "workspace",
        role: Role | None = None,
    ) -> list[SecretRegistration]:
        async with SecretsService.with_session(role=role) as service:
            if scope == "workspace":
                secrets: Sequence[BaseSecret] = await service.list_secrets()
            else:
                secrets = await service.list_org_secrets()
        encryption_key = get_db_encryption_key()
        registrations: list[SecretRegistration] = []
        for secret in secrets:
            if environment is not None and secret.environment != environment:
                continue
            try:
                keys = tuple(
                    kv.key
                    for kv in decrypt_keyvalues(
                        secret.encrypted_keys, key=encryption_key
                    )
                )
            except Exception:
                # Corrupted rows are still listed so operators can spot them.
                keys = ()
            registrations.append(
                SecretRegistration(
                    name=secret.name,
                    keys=keys,
                    type=SecretType(secret.type),
                    environment=secret.environment,
                )
            )
        return registrations

    async def _fetch_secrets(
        self,
        names: set[str],
        *,
        environment: str,
        scope: SecretScope,
        role: Role | None,
    ) -> Sequence[BaseSecret]:
        async with SecretsService.with_session(role=role) as service:
            if scope == "workspace":
                return await service.search_secrets(
                    SecretSearch(names=names, environment=environment)
                )
            secrets: list[BaseSecret] = []
            for name in names:
                try:
                    secrets.append(
                        await service.get_org_secret_by_name(name, environment)
                    )
                except TracecatNotFoundError:
                    continue
            return secrets
