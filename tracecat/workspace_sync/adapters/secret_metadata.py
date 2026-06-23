"""Secret metadata resource adapter (key names only; never secret values)."""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from pydantic import BaseModel, SecretStr
from sqlalchemy import select

from tracecat.db.models import Secret
from tracecat.secrets.enums import SecretType
from tracecat.secrets.schemas import SecretKeyValue
from tracecat.secrets.service import SecretsService
from tracecat.service import BaseWorkspaceService
from tracecat.workspace_sync.adapters.base import (
    EnvironmentYamlAdapter,
    ImportedResource,
    ProjectedResource,
    ResourceDependencyRefs,
    ResourceProjection,
    unique_environment_source_id,
)
from tracecat.workspace_sync.enums import SyncResourceType
from tracecat.workspace_sync.schemas import (
    SECRET_METADATA_ROOT,
    SecretMetadataResourceSpec,
    WorkspaceSpec,
)


class SecretMetadataAdapter(EnvironmentYamlAdapter):
    """Sync adapter for secret metadata: key names only, never secret values."""

    resource_type = SyncResourceType.SECRET_METADATA
    spec_attr = "secret_metadata"
    model = SecretMetadataResourceSpec
    root = SECRET_METADATA_ROOT

    async def project(
        self, workspace_service: BaseWorkspaceService
    ) -> ResourceProjection:
        """Project secrets into specs, emitting only key names, not their values."""
        stmt = self._projection_stmt(workspace_service)
        secrets = list((await workspace_service.session.execute(stmt)).scalars().all())
        return await self._projection_from_secrets(workspace_service, secrets)

    async def project_dependency_refs(
        self,
        workspace_service: BaseWorkspaceService,
        refs: ResourceDependencyRefs,
    ) -> ResourceProjection:
        """Project secret metadata selected directly or referenced by name."""
        if refs.select_all:
            return await self.project(workspace_service)
        if (
            not refs.local_ids
            and not refs.source_ids
            and not refs.names
            and not refs.environment_names
        ):
            return ResourceProjection(specs={}, resources=[])

        local_ids = set(refs.local_ids)
        if refs.source_ids:
            local_ids.update(
                (
                    await self.local_ids_by_source_id(
                        workspace_service,
                        refs.source_ids,
                    )
                ).values()
            )
        predicates = []
        if local_ids:
            predicates.append(Secret.id.in_(local_ids))
        if refs.names:
            predicates.append(Secret.name.in_(refs.names))
        for environment, name in sorted(refs.environment_names):
            predicates.append(
                sa.and_(
                    Secret.environment == environment,
                    Secret.name == name,
                )
            )
        if not predicates:
            return ResourceProjection(specs={}, resources=[])
        stmt = self._projection_stmt(workspace_service).where(sa.or_(*predicates))
        secrets = list((await workspace_service.session.execute(stmt)).scalars().all())
        return await self._projection_from_secrets(workspace_service, secrets)

    def _projection_stmt(
        self, workspace_service: BaseWorkspaceService
    ) -> sa.Select[tuple[Secret]]:
        """Build the base secret metadata projection query."""
        return (
            select(Secret)
            .where(Secret.workspace_id == workspace_service.workspace_id)
            .order_by(Secret.environment.asc(), Secret.name.asc(), Secret.id.asc())
        )

    async def _projection_from_secrets(
        self,
        workspace_service: BaseWorkspaceService,
        secrets: list[Secret],
    ) -> ResourceProjection:
        """Build sync specs from secret rows."""
        secret_service = SecretsService(
            session=workspace_service.session, role=workspace_service.role
        )
        source_ids_by_local_id = await self.source_ids_by_local_id(workspace_service)
        specs: dict[str, BaseModel] = {}
        resources: list[ProjectedResource] = []
        reserved: set[str] = set(source_ids_by_local_id.values())
        for secret in secrets:
            source_id = source_ids_by_local_id.get(secret.id)
            if source_id is None:
                source_id = unique_environment_source_id(
                    secret.environment,
                    secret.name,
                    reserved=reserved,
                )
            reserved.add(source_id)
            keys = sorted(
                key_value.key
                for key_value in secret_service.decrypt_keys(secret.encrypted_keys)
            )
            specs[source_id] = SecretMetadataResourceSpec(
                id=source_id,
                name=secret.name,
                environment=secret.environment,
                secret_type=secret.type,
                keys=keys,
                tags=sorted((secret.tags or {}).keys()),
                description=secret.description,
            )
            resources.append(self.projected_resource(source_id, secret.id))
        return ResourceProjection(specs=specs, resources=resources)

    async def import_specs(
        self,
        workspace_service: BaseWorkspaceService,
        workspace_spec: WorkspaceSpec,
    ) -> list[ImportedResource]:
        """Reconcile secret metadata specs, preserving existing key values.

        Existing values are retained for keys already present on the secret;
        keys new to the spec are created with an empty value to be filled in
        later.
        """
        secret_metadata = workspace_spec.secret_metadata
        imported: list[ImportedResource] = []
        secret_service = SecretsService(
            session=workspace_service.session, role=workspace_service.role
        )
        for source_id, spec in sorted(secret_metadata.items()):
            secret = await self._secret_for_import(
                workspace_service,
                source_id=source_id,
                spec=spec,
            )
            existing_values: dict[str, SecretStr] = {}
            if secret is not None:
                existing_values = {
                    key_value.key: key_value.value
                    for key_value in secret_service.decrypt_keys(secret.encrypted_keys)
                }

            key_values = [
                SecretKeyValue(
                    key=key,
                    value=existing_values.get(key, SecretStr("")),
                )
                for key in spec.keys
            ]
            encrypted_keys = secret_service.encrypt_keys(key_values)
            secret_type = SecretType(spec.secret_type or SecretType.CUSTOM.value).value
            tags = dict.fromkeys(spec.tags, "") if spec.tags else None
            if secret is None:
                secret = Secret(
                    workspace_id=workspace_service.workspace_id,
                    name=spec.name,
                    type=secret_type,
                    encrypted_keys=encrypted_keys,
                    environment=spec.environment,
                    tags=tags,
                    description=spec.description,
                )
            else:
                secret.name = spec.name
                secret.environment = spec.environment
                secret.type = secret_type
                secret.encrypted_keys = encrypted_keys
                secret.tags = tags
                secret.description = spec.description
            workspace_service.session.add(secret)
            await workspace_service.session.flush()
            imported.append(self.imported_resource(source_id, secret.id))
        return imported

    async def _secret_for_import(
        self,
        workspace_service: BaseWorkspaceService,
        *,
        source_id: str,
        spec: SecretMetadataResourceSpec,
    ) -> Secret | None:
        """Resolve the existing secret a spec maps to, by source id then name/env.

        When matched by source id, verifies ``spec``'s name and environment are
        still free before reusing the row. Returns ``None`` when nothing matches.
        """
        secret = await self._secret_by_source_id(workspace_service, source_id=source_id)
        if secret is not None:
            await self._ensure_name_environment_available(
                workspace_service,
                source_id=source_id,
                name=spec.name,
                environment=spec.environment,
                secret_id=secret.id,
            )
            return secret

        return await workspace_service.session.scalar(
            select(Secret).where(
                Secret.workspace_id == workspace_service.workspace_id,
                Secret.name == spec.name,
                Secret.environment == spec.environment,
            )
        )

    async def _secret_by_source_id(
        self,
        workspace_service: BaseWorkspaceService,
        *,
        source_id: str,
    ) -> Secret | None:
        """Load the secret mapped to ``source_id`` via the sync mapping, if any."""
        local_id = await self.local_id_for_source_id(workspace_service, source_id)
        if local_id is None:
            return None

        return await workspace_service.session.scalar(
            select(Secret).where(
                Secret.workspace_id == workspace_service.workspace_id,
                Secret.id == local_id,
            )
        )

    async def _ensure_name_environment_available(
        self,
        workspace_service: BaseWorkspaceService,
        *,
        source_id: str,
        name: str,
        environment: str,
        secret_id: uuid.UUID,
    ) -> None:
        """Raise if another secret already owns ``name`` in ``environment``."""
        conflict_id = await workspace_service.session.scalar(
            select(Secret.id).where(
                Secret.workspace_id == workspace_service.workspace_id,
                Secret.name == name,
                Secret.environment == environment,
                Secret.id != secret_id,
            )
        )
        if conflict_id is None:
            return

        raise ValueError(
            f"Secret metadata sync source id {source_id!r} cannot use "
            f"{environment!r}/{name!r} because another secret already uses it."
        )
