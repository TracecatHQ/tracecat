from __future__ import annotations

import os
from collections.abc import Sequence

from pydantic import SecretStr
from sqlalchemy.exc import MultipleResultsFound, NoResultFound
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat import config
from tracecat.db.schemas import BaseSecret, OrganizationSecret, Secret
from tracecat.identifiers import SecretID
from tracecat.logger import logger
from tracecat.registry.constants import GIT_SSH_KEY_SECRET_NAME
from tracecat.secrets.constants import DEFAULT_SECRETS_ENVIRONMENT
from tracecat.secrets.encryption import decrypt_keyvalues, encrypt_keyvalues
from tracecat.secrets.enums import SecretType
from tracecat.secrets.models import (
    SecretCreate,
    SecretKeyValue,
    SecretSearch,
    SecretUpdate,
)
from tracecat.service import BaseService
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatAuthorizationError, TracecatNotFoundError


class SecretsService(BaseService):
    """Secrets manager service."""

    service_name = "secrets"

    def __init__(self, session: AsyncSession, role: Role | None = None):
        super().__init__(session, role=role)
        try:
            self._encryption_key = os.environ["TRACECAT__DB_ENCRYPTION_KEY"]
        except KeyError as e:
            raise KeyError("TRACECAT__DB_ENCRYPTION_KEY is not set") from e

    def decrypt_keys(self, encrypted_keys: bytes) -> list[SecretKeyValue]:
        """Decrypt and return the keys for a secret."""
        return decrypt_keyvalues(encrypted_keys, key=self._encryption_key)

    def encrypt_keys(self, keys: list[SecretKeyValue]) -> bytes:
        """Encrypt and return the keys for a secret."""
        return encrypt_keyvalues(keys, key=self._encryption_key)

    # === Base secrets ===

    async def _update_secret(self, secret: BaseSecret, params: SecretUpdate) -> None:
        """Update a base secret."""
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

    async def _delete_secret(self, secret: BaseSecret) -> None:
        """Delete a base secret."""
        await self.session.delete(secret)
        await self.session.commit()

    # === Workspace secrets ===

    async def list_secrets(
        self, *, types: set[SecretType] | None = None
    ) -> Sequence[Secret]:
        """List all workspace secrets."""

        statement = select(Secret).where(Secret.owner_id == self.role.workspace_id)
        if types:
            statement = statement.where(col(Secret.type).in_(types))
        result = await self.session.exec(statement)
        return result.all()

    async def get_secret(self, secret_id: SecretID) -> Secret:
        """Get a workspace secret by ID."""

        statement = select(Secret).where(
            Secret.owner_id == self.role.workspace_id,
            Secret.id == secret_id,
        )
        result = await self.session.exec(statement)
        try:
            return result.one()
        except MultipleResultsFound as e:
            logger.error(
                "Multiple secrets found",
                secret_id=secret_id,
                owner_id=self.role.workspace_id,
            )
            raise TracecatNotFoundError(
                "Multiple secrets found when searching by ID"
            ) from e
        except NoResultFound as e:
            logger.error(
                "Secret not found",
                secret_id=secret_id,
                owner_id=self.role.workspace_id,
            )
            raise TracecatNotFoundError(
                "Secret not found when searching by ID. Please check that the ID was correctly input."
            ) from e

    async def get_secret_by_name(
        self,
        secret_name: str,
        environment: str | None = None,
    ) -> Secret:
        """Get a workspace secret by name.

        Args:
            secret_name: The name of the secret to retrieve
            environment: Optional environment to filter by. If provided, only returns secrets for that environment.

        Returns:
            The matching Secret object

        Raises:
            TracecatNotFoundError: If no secret is found with the given name/environment or if multiple secrets are found
        """

        statement = select(Secret).where(
            Secret.owner_id == self.role.workspace_id,
            Secret.name == secret_name,
        )
        if environment:
            statement = statement.where(Secret.environment == environment)
        result = await self.session.exec(statement)
        try:
            return result.one()
        except MultipleResultsFound as e:
            raise TracecatNotFoundError(
                "Multiple secrets found when searching by name."
                f" Expected one secret {secret_name!r} (env: {environment!r}) only."
            ) from e
        except NoResultFound as e:
            raise TracecatNotFoundError(
                f"Secret {secret_name!r} (env: {environment!r}) not found when searching by name."
                " Please double check that the name was correctly input."
            ) from e

    async def create_secret(self, params: SecretCreate) -> None:
        """Create a workspace secret."""
        owner_id = self.role.workspace_id
        if owner_id is None:
            raise TracecatAuthorizationError(
                "Workspace ID is required to create a secret in a workspace"
            )
        secret = Secret(
            owner_id=owner_id,
            name=params.name,
            type=params.type,
            description=params.description,
            tags=params.tags,
            encrypted_keys=self.encrypt_keys(params.keys),
            environment=params.environment,
        )
        self.session.add(secret)
        await self.session.commit()

    async def update_secret(self, secret: Secret, params: SecretUpdate) -> None:
        """Update a workspace secret."""

        await self._update_secret(secret=secret, params=params)

    async def delete_secret(self, secret: Secret) -> None:
        """Delete a workspace secret."""

        await self._delete_secret(secret)

    async def search_secrets(self, params: SecretSearch) -> Sequence[Secret]:
        """Search workspace secrets."""
        if not any((params.ids, params.names, params.environment)):
            return []

        owner_id = self.role.workspace_id
        if owner_id is None:
            raise TracecatAuthorizationError(
                "Workspace ID is required to search secrets"
            )
        stmt = select(Secret).where(Secret.owner_id == owner_id)
        fields = params.model_dump(exclude_unset=True)
        self.logger.info("Searching secrets", set_fields=fields)

        if ids := fields.get("ids"):
            stmt = stmt.where(col(Secret.id).in_(ids))
        if names := fields.get("names"):
            stmt = stmt.where(col(Secret.name).in_(names))
        if "environment" in fields:
            stmt = stmt.where(Secret.environment == fields["environment"])

        result = await self.session.exec(stmt)
        return result.all()

    # === Organization secrets ===

    async def list_org_secrets(
        self, *, types: set[SecretType] | None = None
    ) -> Sequence[OrganizationSecret]:
        """List all organization secrets."""

        stmt = select(OrganizationSecret).where(
            OrganizationSecret.owner_id == config.TRACECAT__DEFAULT_ORG_ID
        )
        if types:
            stmt = stmt.where(col(OrganizationSecret.type).in_(types))
        result = await self.session.exec(stmt)
        return result.all()

    async def get_org_secret(self, secret_id: SecretID) -> OrganizationSecret:
        """Get an organization secret by ID."""

        statement = select(OrganizationSecret).where(
            OrganizationSecret.owner_id == config.TRACECAT__DEFAULT_ORG_ID,
            OrganizationSecret.id == secret_id,
        )
        result = await self.session.exec(statement)
        return result.one()

    async def get_org_secret_by_name(
        self,
        secret_name: str,
        environment: str | None = None,
    ) -> OrganizationSecret:
        """Retrieve an organization-wide secret by its name."""
        environment = environment or DEFAULT_SECRETS_ENVIRONMENT
        statement = select(OrganizationSecret).where(
            OrganizationSecret.owner_id == config.TRACECAT__DEFAULT_ORG_ID,
            OrganizationSecret.name == secret_name,
            OrganizationSecret.environment == environment,
        )
        result = await self.session.exec(statement)
        try:
            return result.one()
        except MultipleResultsFound as e:
            raise TracecatNotFoundError(
                "Multiple organization secrets found when searching by name."
                f" Expected one secret {secret_name!r} (env: {environment!r}) only."
            ) from e
        except NoResultFound as e:
            raise TracecatNotFoundError(
                f"Organization secret {secret_name!r} (env: {environment!r}) not found when searching by name."
                " Please double check that the name was correctly input."
            ) from e

    async def create_org_secret(self, params: SecretCreate) -> None:
        """Create a new organization secret."""
        secret = OrganizationSecret(
            owner_id=config.TRACECAT__DEFAULT_ORG_ID,
            name=params.name,
            type=params.type,
            description=params.description,
            tags=params.tags,
            encrypted_keys=self.encrypt_keys(params.keys),
            environment=params.environment,
        )
        self.session.add(secret)
        await self.session.commit()

    async def update_org_secret(
        self, secret: OrganizationSecret, params: SecretUpdate
    ) -> None:
        await self._update_secret(secret=secret, params=params)

    async def delete_org_secret(self, org_secret: OrganizationSecret) -> None:
        await self._delete_secret(org_secret)

    async def get_ssh_key(
        self,
        key_name: str = GIT_SSH_KEY_SECRET_NAME,
        environment: str | None = None,
    ) -> SecretStr:
        try:
            secret = await self.get_org_secret_by_name(key_name, environment)
            kv = self.decrypt_keys(secret.encrypted_keys)[0]
            logger.debug("SSH key found", key_name=key_name, key_length=len(kv.value))
            raw_value = kv.value.get_secret_value()
            # SSH keys must end with a newline char otherwise we run into
            # load key errors in librcrypto.
            # https://github.com/openssl/openssl/discussions/21481
            if not raw_value.endswith("\n"):
                raw_value += "\n"
            return SecretStr(raw_value)
        except TracecatNotFoundError as e:
            raise TracecatNotFoundError(
                f"SSH key {key_name} not found. Please check whether this key exists.\n\n"
                " If not, please create a key in your organization's credentials page and try again."
            ) from e
