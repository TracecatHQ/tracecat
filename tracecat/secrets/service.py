from __future__ import annotations

import os
from collections.abc import Sequence
from itertools import chain
from typing import Literal, overload

from sqlalchemy.exc import MultipleResultsFound, NoResultFound
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat import config
from tracecat.db.schemas import BaseSecret, OrganizationSecret, Secret
from tracecat.identifiers import SecretID
from tracecat.logger import logger
from tracecat.registry.constants import GIT_SSH_KEY_SECRET_NAME
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
from tracecat.types.exceptions import TracecatNotFoundError


class SecretsService(BaseService):
    """Secrets manager service."""

    service_name = "secrets"

    def __init__(self, session: AsyncSession, role: Role | None = None):
        super().__init__(session, role=role)
        try:
            self._encryption_key = os.environ["TRACECAT__DB_ENCRYPTION_KEY"]
        except KeyError as e:
            raise KeyError("TRACECAT__DB_ENCRYPTION_KEY is not set") from e

    async def _update_secret(self, secret: BaseSecret, params: SecretUpdate) -> None:
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
        """Delete a secret by name."""
        await self.session.delete(secret)
        await self.session.commit()

    async def list_workspace_secrets(
        self, *, types: set[SecretType] | None = None
    ) -> Sequence[Secret]:
        statement = select(Secret).where(Secret.owner_id == self.role.workspace_id)
        if types:
            types_set = set(types)
            statement = statement.where(Secret.type.in_(types_set))  # type: ignore
        result = await self.session.exec(statement)
        return result.all()

    async def list_secrets(
        self, *, types: set[SecretType] | None = None
    ) -> Sequence[BaseSecret]:
        """List secrets.

        Visibility
        ---------
        - If no workspace ID is set, list only the organization secrets.
        """
        org_secrets = await self.list_organization_secrets(types=types)

        if self.role.workspace_id is not None:
            workspace_secrets = await self.list_workspace_secrets(types=types)
        else:
            workspace_secrets = []

        # Combine and return the results
        secrets = list(chain(org_secrets, workspace_secrets))
        self.logger.warning("Listed secrets", secrets=secrets)
        return secrets

    @overload
    async def get_secret_by_id(
        self, secret_id: str, raise_on_error: Literal[True]
    ) -> BaseSecret: ...

    @overload
    async def get_secret_by_id(
        self, secret_id: str, raise_on_error: Literal[False]
    ) -> BaseSecret | None: ...

    async def get_secret_by_id(
        self, secret_id: SecretID, raise_on_error: bool = False
    ) -> BaseSecret | None:
        owner_id, secret_cls = self._get_secret_owner_and_cls()
        statement = select(secret_cls).where(
            secret_cls.owner_id == owner_id, secret_cls.id == secret_id
        )
        result = await self.session.exec(statement)
        try:
            return result.one()
        except MultipleResultsFound as e:
            logger.error(
                "Multiple secrets found",
                secret_id=secret_id,
                owner_id=owner_id,
                cls=secret_cls,
            )
            if raise_on_error:
                raise MultipleResultsFound(
                    "Multiple secrets found when searching by ID"
                ) from e
        except NoResultFound as e:
            logger.error(
                "Secret not found",
                secret_id=secret_id,
                owner_id=owner_id,
                cls=secret_cls,
            )
            if raise_on_error:
                raise NoResultFound(
                    "Secret not found when searching by ID. Please check that the ID was correctly input."
                ) from e
        return None

    @overload
    async def get_secret_by_name(
        self,
        secret_name: str,
        raise_on_error: Literal[True],
        environment: str | None = None,
    ) -> BaseSecret: ...

    @overload
    async def get_secret_by_name(
        self,
        secret_name: str,
        raise_on_error: Literal[False],
        environment: str | None = None,
    ) -> BaseSecret | None: ...

    async def get_secret_by_name(
        self,
        secret_name: str,
        raise_on_error: bool = False,
        environment: str | None = None,
    ) -> BaseSecret | None:
        """
        Retrieve a secret by its name.

        Parameters
        ----------
        secret_name : str
            The name of the secret to retrieve.
        raise_on_error : bool, optional
            If True, raise exceptions for multiple results or no results found.
            Default is False.
        environment : str | None, optional
            The environment to filter the secret search. If None, search across
            all environments. Default is None.

        Returns
        -------
        Secret | None
            The retrieved Secret object if found, None otherwise.

        Raises
        ------
        MultipleResultsFound
            If multiple secrets are found and raise_on_error is True.
        NoResultFound
            If no secret is found and raise_on_error is True.

        Notes
        -----
        This method queries the database for a secret with the given name
        and optionally filters by environment. It handles cases where
        multiple secrets or no secrets are found based on the raise_on_error flag.
        """
        owner_id, secret_cls = self._get_secret_owner_and_cls()
        statement = select(secret_cls).where(
            secret_cls.owner_id == owner_id,
            secret_cls.name == secret_name,
        )
        if environment:
            statement = statement.where(secret_cls.environment == environment)
        result = await self.session.exec(statement)
        try:
            return result.one()
        except MultipleResultsFound as e:
            if raise_on_error:
                raise MultipleResultsFound(
                    "Multiple secrets found when searching by name."
                    f" Expected one secret {secret_name!r} (env: {environment!r}) only."
                ) from e
        except NoResultFound as e:
            if raise_on_error:
                raise NoResultFound(
                    f"Secret {secret_name!r} (env: {environment!r}) not found when searching by name."
                    " Please double check that the name was correctly input."
                ) from e
        return None

    async def create_secret(self, params: SecretCreate) -> None:
        owner_id, secret_cls = self._get_secret_owner_and_cls()
        secret = secret_cls(
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

    async def update_secret_by_name(
        self, secret_name: str, params: SecretUpdate
    ) -> None:
        secret = await self.get_secret_by_name(secret_name, raise_on_error=True)
        await self._update_secret(secret=secret, params=params)

    async def update_secret_by_id(
        self, secret_id: SecretID, params: SecretUpdate
    ) -> None:
        secret = await self.get_secret_by_id(secret_id, raise_on_error=True)
        await self._update_secret(secret=secret, params=params)

    async def delete_secret_by_id(self, secret_id: SecretID) -> None:
        secret = await self.get_secret_by_id(secret_id, raise_on_error=True)
        await self._delete_secret(secret)

    def decrypt_keys(self, encrypted_keys: bytes) -> list[SecretKeyValue]:
        """Decrypt and return the keys for a secret."""
        return decrypt_keyvalues(encrypted_keys, key=self._encryption_key)

    def encrypt_keys(self, keys: list[SecretKeyValue]) -> bytes:
        """Encrypt and return the keys for a secret."""
        return encrypt_keyvalues(keys, key=self._encryption_key)

    async def search_secrets(self, params: SecretSearch) -> Sequence[BaseSecret]:
        """Search secrets by name."""
        if not any((params.ids, params.names, params.environment)):
            return []

        owner_id, secret_cls = self._get_secret_owner_and_cls()
        statement = select(secret_cls).where(secret_cls.owner_id == owner_id)
        fields = params.model_dump(exclude_unset=True)
        self.logger.info("Searching secrets", set_fields=fields)

        if ids := fields.get("ids"):
            statement = statement.where(col(secret_cls.id).in_(ids))
        if names := fields.get("names"):
            statement = statement.where(col(secret_cls.name).in_(names))
        if "environment" in fields:
            statement = statement.where(secret_cls.environment == fields["environment"])

        result = await self.session.exec(statement)
        return result.all()

    """Organization secrets"""

    async def create_organization_secret(self, params: SecretCreate) -> None:
        """Create a new organization-wide secret."""
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

    async def get_organization_secret_by_name(
        self,
        secret_name: str,
        raise_on_error: bool = False,
        environment: str | None = None,
    ) -> OrganizationSecret | None:
        """Retrieve an organization-wide secret by its name."""
        statement = select(OrganizationSecret).where(
            OrganizationSecret.owner_id == config.TRACECAT__DEFAULT_ORG_ID,
            OrganizationSecret.name == secret_name,
        )
        if environment:
            statement = statement.where(OrganizationSecret.environment == environment)
        result = await self.session.exec(statement)
        try:
            return result.one()
        except MultipleResultsFound as e:
            if raise_on_error:
                raise MultipleResultsFound(
                    "Multiple organization secrets found when searching by name."
                    f" Expected one secret {secret_name!r} (env: {environment!r}) only."
                ) from e
        except NoResultFound as e:
            if raise_on_error:
                raise NoResultFound(
                    f"Organization secret {secret_name!r} (env: {environment!r}) not found when searching by name."
                    " Please double check that the name was correctly input."
                ) from e
        return None

    async def update_organization_secret_by_name(
        self, secret_name: str, params: SecretUpdate
    ) -> None:
        """Update an organization-wide secret by its name."""
        secret = await self.get_organization_secret_by_name(
            secret_name, raise_on_error=True
        )
        await self._update_secret(secret=secret, params=params)

    # Delete
    async def delete_organization_secret_by_name(self, secret_name: str) -> None:
        """Delete an organization-wide secret by its name."""
        secret = await self.get_organization_secret_by_name(
            secret_name, raise_on_error=True
        )
        await self._delete_secret(secret)

    async def list_organization_secrets(
        self, *, types: set[SecretType] | None = None
    ) -> Sequence[OrganizationSecret]:
        """List all organization-wide secrets."""
        statement = select(OrganizationSecret).where(
            OrganizationSecret.owner_id == config.TRACECAT__DEFAULT_ORG_ID
        )
        if types:
            statement = statement.where(OrganizationSecret.type.in_(types))
        result = await self.session.exec(statement)
        return result.all()

    def _get_secret_owner_and_cls(self) -> tuple[int, type[BaseSecret]]:
        if self.role.workspace_id is not None:
            return self.role.workspace_id, Secret
        return config.TRACECAT__DEFAULT_ORG_ID, OrganizationSecret

    async def get_ssh_key(
        self, key_name: str = GIT_SSH_KEY_SECRET_NAME
    ) -> SecretKeyValue:
        # NOTE: Don't set the workspace_id, as we want to search for
        # organization secrets if it's not set.
        logger.info("Getting SSH key", key_name=key_name, role=self.role)
        try:
            secret = await self.get_secret_by_name(key_name, raise_on_error=True)
        except NoResultFound as e:
            raise TracecatNotFoundError(
                f"SSH key {key_name} not found. Please check whether this key exists.\n\n"
                " If not, please create a key in your organization's credentials page and try again."
            ) from e
        return self.decrypt_keys(secret.encrypted_keys)[0]
