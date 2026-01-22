from __future__ import annotations

from collections.abc import Sequence

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.exc import MultipleResultsFound, NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.audit.logger import audit_log
from tracecat.auth.types import Role
from tracecat.db.models import BaseSecret, OrganizationSecret, Secret
from tracecat.exceptions import (
    TracecatAuthorizationError,
    TracecatCredentialsNotFoundError,
    TracecatNotFoundError,
)
from tracecat.identifiers import SecretID, WorkspaceID
from tracecat.logger import logger
from tracecat.registry.constants import REGISTRY_GIT_SSH_KEY_SECRET_NAME
from tracecat.secrets.constants import DEFAULT_SECRETS_ENVIRONMENT
from tracecat.secrets.encryption import decrypt_keyvalues, encrypt_keyvalues
from tracecat.secrets.enums import SecretType
from tracecat.secrets.schemas import (
    SecretCreate,
    SecretKeyValue,
    SecretSearch,
    SecretUpdate,
    SSHKeyTarget,
    validate_ca_cert_values,
    validate_mtls_key_values,
    validate_ssh_key_values,
)
from tracecat.service import BaseService


class SecretsService(BaseService):
    """Secrets manager service."""

    service_name = "secrets"
    _encryption_key: str

    def __init__(self, session: AsyncSession, role: Role | None = None):
        super().__init__(session, role=role)
        encryption_key = config.TRACECAT__DB_ENCRYPTION_KEY
        if not encryption_key:
            raise KeyError("TRACECAT__DB_ENCRYPTION_KEY is not set")
        self._encryption_key = encryption_key

    def _require_workspace_id(self) -> WorkspaceID:
        """Get workspace_id, raising if role or workspace_id is None."""
        if self.role is None or self.role.workspace_id is None:
            raise TracecatAuthorizationError(
                "Workspace context required for this operation"
            )
        return self.role.workspace_id

    def decrypt_keys(self, encrypted_keys: bytes) -> list[SecretKeyValue]:
        """Decrypt and return the keys for a secret."""
        return decrypt_keyvalues(encrypted_keys, key=self._encryption_key)

    def encrypt_keys(self, keys: list[SecretKeyValue]) -> bytes:
        """Encrypt and return the keys for a secret."""
        return encrypt_keyvalues(keys, key=self._encryption_key)

    # === Base secrets ===

    async def _update_secret(self, secret: BaseSecret, params: SecretUpdate) -> None:
        """Update a base secret."""
        existing_type = SecretType(secret.type)
        if existing_type == SecretType.SSH_KEY:
            if params.type is not None and SecretType(params.type) != existing_type:
                raise ValueError(
                    "SSH key secrets cannot change type. Delete and recreate the secret."
                )
            if params.keys is not None:
                raise ValueError(
                    "SSH key secrets are write-once. Delete and recreate to rotate the key."
                )
        elif existing_type == SecretType.MTLS:
            if params.type is not None and SecretType(params.type) != existing_type:
                raise ValueError(
                    "mTLS secrets cannot change type. Delete and recreate the secret."
                )
        elif existing_type == SecretType.CA_CERT:
            if params.type is not None and SecretType(params.type) != existing_type:
                raise ValueError(
                    "CA certificate secrets cannot change type. Delete and recreate the secret."
                )
        elif params.type == SecretType.SSH_KEY:
            raise ValueError(
                "SSH key secrets must be created with their key value. Delete and recreate the secret instead."
            )
        elif params.type == SecretType.MTLS:
            raise ValueError(
                "mTLS secrets must be created with their key values. Delete and recreate the secret instead."
            )
        elif params.type == SecretType.CA_CERT:
            raise ValueError(
                "CA certificate secrets must be created with their key values. Delete and recreate the secret instead."
            )
        set_fields = params.model_dump(exclude_unset=True)
        # Handle keys separately
        if keys := set_fields.pop("keys", None):
            # Decrypt existing keys to a dictionary for easy lookup
            existing_keys = {
                kv.key: kv.value for kv in self.decrypt_keys(secret.encrypted_keys)
            }

            # Create new key-value pairs, preserving existing values when the new value is empty
            merged_keyvalues = []
            for kv in keys:
                key = kv["key"]
                value = kv["value"]

                # If value is empty and the key already exists, keep the existing value
                if not value and key in existing_keys:
                    merged_keyvalues.append(
                        SecretKeyValue(key=key, value=existing_keys[key])
                    )
                else:
                    merged_keyvalues.append(SecretKeyValue(**kv))

            if existing_type == SecretType.MTLS:
                validate_mtls_key_values(merged_keyvalues)
            elif existing_type == SecretType.CA_CERT:
                validate_ca_cert_values(merged_keyvalues)

            secret.encrypted_keys = encrypt_keyvalues(
                merged_keyvalues, key=self._encryption_key
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
        workspace_id = self._require_workspace_id()
        statement = select(Secret).where(Secret.workspace_id == workspace_id)
        if types:
            statement = statement.where(Secret.type.in_(types))
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def get_secret(self, secret_id: SecretID) -> Secret:
        """Get a workspace secret by ID."""
        workspace_id = self._require_workspace_id()
        statement = select(Secret).where(
            Secret.workspace_id == workspace_id,
            Secret.id == secret_id,
        )
        result = await self.session.execute(statement)
        try:
            return result.scalar_one()
        except MultipleResultsFound as e:
            logger.error(
                "Multiple secrets found",
                secret_id=secret_id,
                workspace_id=workspace_id,
            )
            raise TracecatNotFoundError(
                "Multiple secrets found when searching by ID"
            ) from e
        except NoResultFound as e:
            logger.error(
                "Secret not found",
                secret_id=secret_id,
                workspace_id=workspace_id,
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
        workspace_id = self._require_workspace_id()
        statement = select(Secret).where(
            Secret.workspace_id == workspace_id,
            Secret.name == secret_name,
        )
        if environment:
            statement = statement.where(Secret.environment == environment)
        result = await self.session.execute(statement)
        try:
            return result.scalar_one()
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

    @audit_log(resource_type="secret", action="create")
    async def create_secret(self, params: SecretCreate) -> None:
        """Create a workspace secret."""
        workspace_id = self._require_workspace_id()
        if params.type == SecretType.SSH_KEY:
            validate_ssh_key_values(params.keys)
        elif params.type == SecretType.MTLS:
            validate_mtls_key_values(params.keys)
        elif params.type == SecretType.CA_CERT:
            validate_ca_cert_values(params.keys)
        secret = Secret(
            workspace_id=workspace_id,
            name=params.name,
            type=params.type,
            description=params.description,
            tags=params.tags,
            encrypted_keys=self.encrypt_keys(params.keys),
            environment=params.environment,
        )
        self.session.add(secret)
        await self.session.commit()

    @audit_log(resource_type="secret", action="update")
    async def update_secret(self, secret: Secret, params: SecretUpdate) -> None:
        """Update a workspace secret."""

        await self._update_secret(secret=secret, params=params)

    @audit_log(resource_type="secret", action="delete")
    async def delete_secret(self, secret: Secret) -> None:
        """Delete a workspace secret."""

        await self._delete_secret(secret)

    async def search_secrets(self, params: SecretSearch) -> Sequence[Secret]:
        """Search workspace secrets."""
        if not any((params.ids, params.names, params.environment)):
            return []

        workspace_id = self._require_workspace_id()
        stmt = select(Secret).where(Secret.workspace_id == workspace_id)
        fields = params.model_dump(exclude_unset=True)
        self.logger.info("Searching secrets", set_fields=fields)

        if ids := fields.get("ids"):
            stmt = stmt.where(Secret.id.in_(ids))
        if names := fields.get("names"):
            stmt = stmt.where(Secret.name.in_(names))
        if "environment" in fields:
            stmt = stmt.where(Secret.environment == fields["environment"])

        result = await self.session.execute(stmt)
        return result.scalars().all()

    # === Organization secrets ===

    async def list_org_secrets(
        self, *, types: set[SecretType] | None = None
    ) -> Sequence[OrganizationSecret]:
        """List all organization secrets."""

        stmt = select(OrganizationSecret).where(
            OrganizationSecret.organization_id == self.organization_id
        )
        if types:
            stmt = stmt.where(OrganizationSecret.type.in_(types))
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_org_secret(self, secret_id: SecretID) -> OrganizationSecret:
        """Get an organization secret by ID."""

        statement = select(OrganizationSecret).where(
            OrganizationSecret.organization_id == self.organization_id,
            OrganizationSecret.id == secret_id,
        )
        result = await self.session.execute(statement)
        return result.scalar_one()

    async def get_org_secret_by_name(
        self,
        secret_name: str,
        environment: str | None = None,
    ) -> OrganizationSecret:
        """Retrieve an organization-wide secret by its name."""
        environment = environment or DEFAULT_SECRETS_ENVIRONMENT
        statement = select(OrganizationSecret).where(
            OrganizationSecret.organization_id == self.organization_id,
            OrganizationSecret.name == secret_name,
            OrganizationSecret.environment == environment,
        )
        result = await self.session.execute(statement)
        try:
            return result.scalar_one()
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

    @audit_log(resource_type="organization_secret", action="create")
    async def create_org_secret(self, params: SecretCreate) -> None:
        """Create a new organization secret."""
        if params.type == SecretType.SSH_KEY:
            validate_ssh_key_values(params.keys)
        elif params.type == SecretType.MTLS:
            validate_mtls_key_values(params.keys)
        elif params.type == SecretType.CA_CERT:
            validate_ca_cert_values(params.keys)
        secret = OrganizationSecret(
            organization_id=self.organization_id,
            name=params.name,
            type=params.type,
            description=params.description,
            tags=params.tags,
            encrypted_keys=self.encrypt_keys(params.keys),
            environment=params.environment,
        )
        self.session.add(secret)
        await self.session.commit()

    @audit_log(resource_type="organization_secret", action="update")
    async def update_org_secret(
        self, secret: OrganizationSecret, params: SecretUpdate
    ) -> None:
        await self._update_secret(secret=secret, params=params)

    @audit_log(
        resource_type="organization_secret",
        action="delete",
    )
    async def delete_org_secret(self, org_secret: OrganizationSecret) -> None:
        await self._delete_secret(org_secret)

    async def get_ssh_key(
        self,
        key_name: str | None = None,
        environment: str | None = None,
        target: SSHKeyTarget = "registry",
    ) -> SecretStr:
        match target:
            case "registry":
                return await self.get_registry_ssh_key(key_name, environment)
            case _:
                raise ValueError(f"Invalid target: {target}")

    async def get_registry_ssh_key(
        self, key_name: str | None = None, environment: str | None = None
    ) -> SecretStr:
        try:
            key_name = key_name or REGISTRY_GIT_SSH_KEY_SECRET_NAME
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
            raise TracecatCredentialsNotFoundError(
                f"SSH key {key_name} not found. Please check whether this key exists.\n\n"
                " If not, please create a key in your organization's credentials page and try again."
            ) from e
