"""Secret metadata resource adapter (key names only; never secret values)."""

from __future__ import annotations

import sqlalchemy as sa
from pydantic import BaseModel, SecretStr
from sqlalchemy import select

from tracecat.db.models import Secret
from tracecat.secrets.enums import SecretType
from tracecat.secrets.schemas import SecretKeyValue
from tracecat.secrets.service import SecretsService
from tracecat.workspace_sync.adapters.base import (
    EnvironmentScopedManifestAdapter,
    ImportedResource,
    NameSwapPlan,
    ProjectedResource,
    ResourceDependencyRefs,
    ResourceProjection,
    SyncMappingService,
)
from tracecat.workspace_sync.enums import SyncResourceType
from tracecat.workspace_sync.schemas import (
    SECRET_METADATA_ROOT,
    SecretMetadataResourceSpec,
    WorkspaceSpec,
)


class SecretMetadataAdapter(EnvironmentScopedManifestAdapter):
    """Sync adapter for secret metadata: key names only, never secret values."""

    resource_type = SyncResourceType.SECRET_METADATA
    spec_attr = "secret_metadata"
    model = SecretMetadataResourceSpec
    read_scope = "secret:read"
    create_scope = "secret:create"
    update_scope = "secret:update"
    root = SECRET_METADATA_ROOT
    import_identity_attrs = ("environment", "name")
    import_identity_noun = "target"

    async def project(
        self, workspace_service: SyncMappingService
    ) -> ResourceProjection:
        """Project secrets into specs, emitting only key names, not their values."""
        stmt = self._projection_stmt(workspace_service)
        secrets = list((await workspace_service.session.execute(stmt)).scalars().all())
        return await self._projection_from_secrets(workspace_service, secrets)

    async def project_dependency_refs(
        self,
        workspace_service: SyncMappingService,
        refs: ResourceDependencyRefs,
    ) -> ResourceProjection:
        """Project secret metadata selected directly or referenced by name."""
        # "Select all" short-circuits to the full projection.
        if refs.select_all:
            return await self.project(workspace_service)
        # No selectors of any kind means there is nothing to project.
        if not refs.local_ids and not refs.source_ids and not refs.names:
            return ResourceProjection(specs={}, resources=[])

        local_ids = set(refs.local_ids)
        # Resolve source ids to their local secret ids and fold them in, so all
        # id-based selectors collapse into a single set of local ids.
        if refs.source_ids:
            local_ids.update(
                (
                    await self.local_ids_by_source_id(
                        workspace_service,
                        refs.source_ids,
                    )
                ).values()
            )
        # Each selector kind contributes its own predicate; they are ORed below
        # so a secret matching any selector is projected.
        predicates = []
        if local_ids:
            predicates.append(Secret.id.in_(local_ids))
        if refs.names:
            predicates.append(Secret.name.in_(refs.names))
        # Selectors may exist yet resolve to nothing (e.g. unknown source ids).
        if not predicates:
            return ResourceProjection(specs={}, resources=[])
        stmt = self._projection_stmt(workspace_service).where(sa.or_(*predicates))
        secrets = list((await workspace_service.session.execute(stmt)).scalars().all())
        return await self._projection_from_secrets(workspace_service, secrets)

    def _projection_stmt(
        self, workspace_service: SyncMappingService
    ) -> sa.Select[tuple[Secret]]:
        """Build the base secret metadata projection query."""
        return (
            select(Secret)
            .where(Secret.workspace_id == workspace_service.workspace_id)
            .order_by(Secret.environment.asc(), Secret.name.asc(), Secret.id.asc())
        )

    async def _projection_from_secrets(
        self,
        workspace_service: SyncMappingService,
        secrets: list[Secret],
    ) -> ResourceProjection:
        """Build sync specs from secret rows."""
        secret_service = SecretsService(
            session=workspace_service.session, role=workspace_service.role
        )
        assigner = await self.source_id_assigner(workspace_service)
        specs: dict[str, BaseModel] = {}
        resources: list[ProjectedResource] = []
        for secret in secrets:
            source_id = assigner.assign_environment(
                secret.id, secret.environment, secret.name
            )
            # Decrypt only to read the key NAMES; secret values are never read
            # back out or serialized into the projected spec.
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
        workspace_service: SyncMappingService,
        workspace_spec: WorkspaceSpec,
    ) -> list[ImportedResource]:
        """Reconcile secret metadata specs, preserving existing key values.

        Existing values are retained for keys already present on the secret;
        keys new to the spec are created with an empty value to be filled in
        later.
        """
        secret_metadata = workspace_spec.secret_metadata
        if not secret_metadata:
            return []

        imported: list[ImportedResource] = []
        secret_service = SecretsService(
            session=workspace_service.session, role=workspace_service.role
        )
        # Secrets are unique per (environment, name): reject duplicate targets,
        # then park identity-changing rows under temporary names so an in-batch
        # swap doesn't trip the unique constraint mid-flush.
        swap = await self.plan_name_swap(
            workspace_service,
            targets={
                source_id: spec.name for source_id, spec in secret_metadata.items()
            },
            target_scopes={
                source_id: spec.environment
                for source_id, spec in secret_metadata.items()
            },
            model=Secret,
            name_column=Secret.name,
            scope_column=Secret.environment,
            noun="name",
            kind_label="Secret metadata",
            owner_label="secret",
        )

        for source_id, spec in sorted(secret_metadata.items()):
            secret = await self._secret_for_import(
                workspace_service,
                source_id=source_id,
                spec=spec,
                swap=swap,
            )
            # Pull the current decrypted values so existing keys keep their
            # secret values across the sync; the spec only carries key names.
            existing_values: dict[str, SecretStr] = {}
            if secret is not None:
                existing_values = {
                    key_value.key: key_value.value
                    for key_value in secret_service.decrypt_keys(secret.encrypted_keys)
                }

            # Build the new key set from the spec: preserve the value of any key
            # already present, and give keys new to the spec an empty value.
            key_values = [
                SecretKeyValue(
                    key=key,
                    value=existing_values.get(key, SecretStr("")),
                )
                for key in spec.keys
            ]
            # Re-encrypt the reconciled key/value pairs before persisting.
            encrypted_keys = secret_service.encrypt_keys(key_values)
            secret_type = SecretType(spec.secret_type or SecretType.CUSTOM.value).value
            # Tags are stored as a name-keyed dict with empty values.
            tags = dict.fromkeys(spec.tags, "") if spec.tags else None
            # No matching row: create a brand-new secret from the spec.
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
                # Matched an existing row: update it in place to the spec.
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
        workspace_service: SyncMappingService,
        *,
        source_id: str,
        spec: SecretMetadataResourceSpec,
        swap: NameSwapPlan[Secret],
    ) -> Secret | None:
        """Resolve the existing secret a spec maps to, by source id then name/env.

        When matched by source id, verifies ``spec``'s name and environment are
        still free before reusing the row. Returns ``None`` when nothing matches.
        """
        # Prefer the sync-mapping match: it pins the spec to the same row across
        # renames, so a spec can move name/environment without losing identity.
        secret = swap.mapped_by_source_id.get(source_id) or (
            await self._secret_by_source_id(workspace_service, source_id=source_id)
        )
        if secret is not None:
            return secret

        # No mapping yet: fall back to matching an existing secret by its
        # (name, environment) identity. Returns None when nothing matches.
        return await workspace_service.session.scalar(
            select(Secret).where(
                Secret.workspace_id == workspace_service.workspace_id,
                Secret.name == spec.name,
                Secret.environment == spec.environment,
            )
        )

    async def _secret_by_source_id(
        self,
        workspace_service: SyncMappingService,
        *,
        source_id: str,
    ) -> Secret | None:
        """Load the secret mapped to ``source_id`` via the sync mapping, if any."""
        return await self._row_by_source_id(
            workspace_service, source_id=source_id, model=Secret
        )
