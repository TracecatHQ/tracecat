"""Secret metadata resource adapter (key names only; never secret values)."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass

import sqlalchemy as sa
from pydantic import BaseModel, SecretStr
from sqlalchemy import select

from tracecat.db.models import Secret
from tracecat.secrets.enums import SecretType
from tracecat.secrets.schemas import SecretKeyValue
from tracecat.secrets.service import SecretsService
from tracecat.service import BaseWorkspaceService
from tracecat.workspace_sync.adapters.base import (
    EnvironmentScopedManifestAdapter,
    ImportedResource,
    ProjectedResource,
    ResourceDependencyRefs,
    ResourceProjection,
)
from tracecat.workspace_sync.enums import SyncResourceType
from tracecat.workspace_sync.schemas import (
    SECRET_METADATA_ROOT,
    SecretMetadataResourceSpec,
    WorkspaceSpec,
)


@dataclass(frozen=True, slots=True)
class SecretMetadataKey:
    """A secret metadata row's uniqueness key within a workspace."""

    environment: str
    name: str


class SecretMetadataAdapter(EnvironmentScopedManifestAdapter):
    """Sync adapter for secret metadata: key names only, never secret values."""

    resource_type = SyncResourceType.SECRET_METADATA
    spec_attr = "secret_metadata"
    model = SecretMetadataResourceSpec
    read_scope = "secret:read"
    update_scope = "secret:update"
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
        workspace_service: BaseWorkspaceService,
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
        target_keys_by_source_id = _target_keys_by_source_id(secret_metadata)
        _ensure_unique_targets(target_keys_by_source_id)
        mapped_secrets = {
            source_id: secret
            for source_id in sorted(secret_metadata)
            if (
                secret := await self._secret_by_source_id(
                    workspace_service,
                    source_id=source_id,
                )
            )
            is not None
        }
        source_ids_by_secret_id = {
            secret.id: source_id for source_id, secret in mapped_secrets.items()
        }
        for source_id, secret in mapped_secrets.items():
            spec = secret_metadata[source_id]
            await self._ensure_name_environment_available(
                workspace_service,
                source_id=source_id,
                name=spec.name,
                environment=spec.environment,
                secret_id=secret.id,
                source_ids_by_secret_id=source_ids_by_secret_id,
                target_keys_by_source_id=target_keys_by_source_id,
            )
        await self._release_changing_mapped_secrets(
            workspace_service,
            secrets_by_source_id=mapped_secrets,
            target_keys_by_source_id=target_keys_by_source_id,
        )

        for source_id, spec in sorted(secret_metadata.items()):
            secret = await self._secret_for_import(
                workspace_service,
                source_id=source_id,
                spec=spec,
                mapped_secret=mapped_secrets.get(source_id),
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
        workspace_service: BaseWorkspaceService,
        *,
        source_id: str,
        spec: SecretMetadataResourceSpec,
        mapped_secret: Secret | None = None,
    ) -> Secret | None:
        """Resolve the existing secret a spec maps to, by source id then name/env.

        When matched by source id, verifies ``spec``'s name and environment are
        still free before reusing the row. Returns ``None`` when nothing matches.
        """
        # Prefer the sync-mapping match: it pins the spec to the same row across
        # renames, so a spec can move name/environment without losing identity.
        secret = mapped_secret or await self._secret_by_source_id(
            workspace_service, source_id=source_id
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
        workspace_service: BaseWorkspaceService,
        *,
        source_id: str,
    ) -> Secret | None:
        """Load the secret mapped to ``source_id`` via the sync mapping, if any."""
        return await self._row_by_source_id(
            workspace_service, source_id=source_id, model=Secret
        )

    async def _ensure_name_environment_available(
        self,
        workspace_service: BaseWorkspaceService,
        *,
        source_id: str,
        name: str,
        environment: str,
        secret_id: uuid.UUID,
        source_ids_by_secret_id: Mapping[uuid.UUID, str] | None = None,
        target_keys_by_source_id: Mapping[str, SecretMetadataKey] | None = None,
    ) -> None:
        """Raise if another secret blocks claiming ``environment``/``name``."""
        # Look for any *other* secret holding this (name, environment) slot.
        conflict_id = await workspace_service.session.scalar(
            select(Secret.id).where(
                Secret.workspace_id == workspace_service.workspace_id,
                Secret.name == name,
                Secret.environment == environment,
                Secret.id != secret_id,
            )
        )
        # Slot is free (or only held by the secret we are reusing): all good.
        if conflict_id is None:
            return

        if source_ids_by_secret_id and target_keys_by_source_id:
            conflict_source_id = source_ids_by_secret_id.get(conflict_id)
            if conflict_source_id is not None and target_keys_by_source_id[
                conflict_source_id
            ] != SecretMetadataKey(environment=environment, name=name):
                return

        raise ValueError(
            f"Secret metadata sync source id {source_id!r} cannot use "
            f"{environment!r}/{name!r} because another secret already uses it."
        )

    async def _release_changing_mapped_secrets(
        self,
        workspace_service: BaseWorkspaceService,
        *,
        secrets_by_source_id: Mapping[str, Secret],
        target_keys_by_source_id: Mapping[str, SecretMetadataKey],
    ) -> None:
        """Park secrets whose identity is changing under temporary names."""
        changed = False
        reserved_names_by_environment = await _reserved_names_by_environment(
            workspace_service
        )
        for source_id, secret in secrets_by_source_id.items():
            if (
                SecretMetadataKey(environment=secret.environment, name=secret.name)
                == target_keys_by_source_id[source_id]
            ):
                continue
            reserved_names = reserved_names_by_environment.setdefault(
                secret.environment,
                set(),
            )
            reserved_names.discard(secret.name)
            secret.name = _unique_temporary_secret_name(secret, reserved_names)
            reserved_names.add(secret.name)
            workspace_service.session.add(secret)
            changed = True
        if changed:
            await workspace_service.session.flush()


def _target_keys_by_source_id(
    secret_metadata: Mapping[str, SecretMetadataResourceSpec],
) -> dict[str, SecretMetadataKey]:
    """Return each source id's desired ``(environment, name)`` target."""
    return {
        source_id: SecretMetadataKey(environment=spec.environment, name=spec.name)
        for source_id, spec in secret_metadata.items()
    }


def _ensure_unique_targets(
    targets_by_source_id: Mapping[str, SecretMetadataKey],
) -> None:
    """Reject a sync batch that targets the same secret identity twice."""
    seen: dict[SecretMetadataKey, str] = {}
    duplicates: list[SecretMetadataKey] = []
    for source_id, target in sorted(targets_by_source_id.items()):
        if target in seen:
            duplicates.append(target)
            continue
        seen[target] = source_id
    if duplicates:
        names = ", ".join(
            f"{target.environment!r}/{target.name!r}" for target in duplicates
        )
        raise ValueError(
            f"Secret metadata sync specs must have unique targets: {names}"
        )


async def _reserved_names_by_environment(
    workspace_service: BaseWorkspaceService,
) -> dict[str, set[str]]:
    """Return in-use secret names grouped by environment for this workspace."""
    rows = (
        await workspace_service.session.execute(
            select(Secret.environment, Secret.name).where(
                Secret.workspace_id == workspace_service.workspace_id
            )
        )
    ).tuples()
    reserved: dict[str, set[str]] = {}
    for environment, name in rows:
        reserved.setdefault(environment, set()).add(name)
    return reserved


def _unique_temporary_secret_name(secret: Secret, reserved_names: set[str]) -> str:
    """Mint a placeholder secret name not present in ``reserved_names``."""
    base = f"__tracecat_sync_tmp_{secret.id.hex}"
    candidate = base
    suffix = 2
    while candidate in reserved_names:
        candidate = f"{base}_{suffix}"
        suffix += 1
    return candidate
