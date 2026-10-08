"""Secret metadata resource adapter (key names only; never secret values)."""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from typing import TypedDict

import sqlalchemy as sa
from pydantic import BaseModel, SecretStr
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from tracecat_ee.secrets.references.service import SecretReferencesService

from tracecat.db.models import OrganizationSecretStore, Secret
from tracecat.exceptions import TracecatAuthorizationError
from tracecat.secrets.enums import SecretSource, SecretType
from tracecat.secrets.schemas import (
    EXPRESSION_SECRET_NAME_PATTERN,
    AwsSecretKeyMapping,
    SecretKeyValue,
)
from tracecat.secrets.service import (
    SecretsService,
    is_external_reference,
    secret_key_names,
)
from tracecat.sync import (
    PullDiagnostic,
    SecretStoreMappingAffectedSecret,
    SecretStoreMappingCandidate,
    SecretStoreMappingRequirement,
    SecretStoreMappingRequirementReason,
)
from tracecat.tiers.entitlements import check_entitlement
from tracecat.tiers.enums import Entitlement
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
from tracecat.workspace_sync.types import CorrelatedSecretStores


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
            .options(selectinload(Secret.store))
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
            # Only key NAMES are read; secret values are never read back out
            # or serialized into the projected spec. AWS-backed rows return
            # their declared keys without any remote call.
            keys = sorted(secret_key_names(secret_service, secret))
            specs[source_id] = SecretMetadataResourceSpec(
                id=source_id,
                name=secret.name,
                environment=secret.environment,
                secret_type=secret.type,
                keys=keys,
                tags=sorted((secret.tags or {}).keys()),
                description=secret.description,
                **_external_reference_fields(secret),
            )
            resources.append(self.projected_resource(source_id, secret.id))
        return ResourceProjection(specs=specs, resources=resources)

    async def correlate_store_names(
        self,
        workspace_service: SyncMappingService,
        secret_metadata: dict[str, SecretMetadataResourceSpec],
        *,
        requested_store_mappings: Mapping[str, uuid.UUID] | None = None,
    ) -> CorrelatedSecretStores:
        """Point AWS-backed specs at authorized stores before preview or import.

        A store name matching an authorized store resolves as is. A chosen store
        replaces the name for every secret that uses it. Names left unmatched are
        returned as requirements without blocking; those secrets import unlinked.
        """
        requested = requested_store_mappings or {}
        by_store: dict[str, list[tuple[str, SecretMetadataResourceSpec]]] = {}
        for source_id, spec in sorted(secret_metadata.items()):
            if spec.source == SecretSource.AWS_SECRETS_MANAGER and spec.store:
                by_store.setdefault(spec.store, []).append((source_id, spec))

        diagnostics = [
            PullDiagnostic(
                workflow_path="",
                workflow_title=None,
                error_type="validation",
                message=(
                    f"Secret store mapping for {name!r} does not appear in this "
                    "repository snapshot."
                ),
                details={
                    "code": "secret_store_mapping_source_not_found",
                    "store": name,
                },
            )
            for name in sorted(set(requested) - set(by_store))
        ]
        if not by_store or not await workspace_service.has_entitlement(
            Entitlement.EXTERNAL_SECRET_STORES
        ):
            return CorrelatedSecretStores(secret_metadata, diagnostics, [])

        stores = await SecretReferencesService(
            session=workspace_service.session, role=workspace_service.role
        ).list_all_authorized_stores()
        stores_by_name = {store.name: store for store in stores}
        stores_by_id = {store.id: store for store in stores}
        candidates = [
            SecretStoreMappingCandidate(
                store_id=store.id,
                name=store.name,
                region=(store.config or {}).get("region"),
            )
            for store in stores
            if store.enabled
        ]
        linked = await self._linked_reference_identities(workspace_service)

        correlated = dict(secret_metadata)
        requirements: list[SecretStoreMappingRequirement] = []
        for name, specs in sorted(by_store.items()):
            target_id = requested.get(name)
            store = (
                stores_by_id.get(target_id)
                if target_id is not None
                else stores_by_name.get(name)
            )
            if store is None:
                reason: SecretStoreMappingRequirementReason = (
                    "invalid_selection" if target_id is not None else "unresolved"
                )
                # Secrets already linked here keep their store on import.
                affected = [
                    (source_id, spec)
                    for source_id, spec in specs
                    if (spec.name, spec.environment) not in linked
                ]
                if not affected and reason == "unresolved":
                    continue
                requirement = self._store_requirement(
                    name, reason, candidates, affected or specs
                )
                requirements.append(requirement)
                if reason == "invalid_selection":
                    diagnostics.append(
                        PullDiagnostic(
                            workflow_path=self.source_path(specs[0][0]),
                            workflow_title=None,
                            error_type="dependency",
                            message=requirement.message,
                            details={
                                "code": "secret_store_mapping_invalid",
                                "store": name,
                            },
                        )
                    )
                continue
            for source_id, spec in specs:
                if spec.remote_reference is not None:
                    try:
                        SecretReferencesService.validate_reference(
                            store, spec.remote_reference
                        )
                    except ValueError as e:
                        diagnostics.append(
                            PullDiagnostic(
                                workflow_path=self.source_path(source_id),
                                workflow_title=None,
                                error_type="validation",
                                message=(
                                    f"Secret {spec.name!r} can't use store "
                                    f"{store.name!r}: {e}"
                                ),
                                details={
                                    "code": "secret_store_reference_invalid",
                                    "store": store.name,
                                },
                            )
                        )
                        continue
                if spec.store != store.name:
                    correlated[source_id] = spec.model_copy(
                        update={"store": store.name}
                    )
        return CorrelatedSecretStores(correlated, diagnostics, requirements)

    def _store_requirement(
        self,
        name: str,
        reason: SecretStoreMappingRequirementReason,
        candidates: list[SecretStoreMappingCandidate],
        specs: list[tuple[str, SecretMetadataResourceSpec]],
    ) -> SecretStoreMappingRequirement:
        if reason == "invalid_selection":
            message = (
                f"The store chosen for {name!r} isn't authorized for this workspace."
            )
        else:
            message = f"No store named {name!r} is authorized for this workspace."
        return SecretStoreMappingRequirement(
            source_store=name,
            reason=reason,
            message=message,
            candidates=candidates,
            affected_secrets=[
                SecretStoreMappingAffectedSecret(
                    secret_name=spec.name,
                    environment=spec.environment,
                    path=self.source_path(source_id),
                )
                for source_id, spec in specs
            ],
        )

    async def _linked_reference_identities(
        self, workspace_service: SyncMappingService
    ) -> set[tuple[str, str]]:
        """Return (name, environment) of AWS-backed secrets linked to a store."""
        rows = await workspace_service.session.execute(
            select(Secret.name, Secret.environment).where(
                Secret.workspace_id == workspace_service.workspace_id,
                Secret.source == SecretSource.AWS_SECRETS_MANAGER.value,
                Secret.store_id.is_not(None),
            )
        )
        return {(row.name, row.environment) for row in rows}

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

        references: SecretReferencesService | None = None
        if any(
            spec.source == SecretSource.AWS_SECRETS_MANAGER
            for spec in secret_metadata.values()
        ):
            await check_entitlement(
                workspace_service.session,
                workspace_service.role,
                Entitlement.EXTERNAL_SECRET_STORES,
            )
            references = SecretReferencesService(
                session=workspace_service.session, role=workspace_service.role
            )
        stores_by_name: dict[str, OrganizationSecretStore | None] = {}

        for source_id, spec in sorted(secret_metadata.items()):
            secret = await self._secret_for_import(
                workspace_service,
                source_id=source_id,
                spec=spec,
                swap=swap,
            )
            if (
                references is not None
                and spec.source == SecretSource.AWS_SECRETS_MANAGER
            ):
                secret = await self._import_external_reference(
                    workspace_service,
                    references=references,
                    stores_by_name=stores_by_name,
                    source_id=source_id,
                    spec=spec,
                    secret=secret,
                )
                imported.append(self.imported_resource(source_id, secret.id))
                continue
            # Pull the current decrypted values so existing keys keep their
            # secret values across the sync; the spec only carries key names.
            existing_values: dict[str, SecretStr] = {}
            if secret is not None and is_external_reference(secret):
                # A spec without a reference (exported before references were
                # synced) can only change metadata. The store owns the values,
                # so reject keys or type that disagree instead of reporting a
                # silent partial import.
                declared_keys = sorted(secret_key_names(secret_service, secret))
                spec_type = SecretType(spec.secret_type or SecretType.CUSTOM.value)
                if sorted(spec.keys) != declared_keys or spec_type != secret.type:
                    raise ValueError(
                        f"Secret metadata sync source id {source_id!r} targets an "
                        f"externally backed secret {secret.name!r}; its keys and "
                        "type must be changed in the target workspace, not synced."
                    )
                _require_reference_name(source_id, spec.name)
                secret.name = spec.name
                secret.environment = spec.environment
                secret.tags = dict.fromkeys(spec.tags, "") if spec.tags else None
                secret.description = spec.description
                workspace_service.session.add(secret)
                await workspace_service.session.flush()
                imported.append(self.imported_resource(source_id, secret.id))
                continue
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

    async def _import_external_reference(
        self,
        workspace_service: SyncMappingService,
        *,
        references: SecretReferencesService,
        stores_by_name: dict[str, OrganizationSecretStore | None],
        source_id: str,
        spec: SecretMetadataResourceSpec,
        secret: Secret | None,
    ) -> Secret:
        """Create or update an AWS-backed secret from its synced reference.

        The store is matched by name among the stores authorized for the
        target workspace. Without a match the secret keeps a store it is
        already linked to, or is imported without one so it can be linked
        manually.
        """
        if spec.remote_reference is None or spec.key_mapping is None:
            raise ValueError(
                f"Secret metadata sync source id {source_id!r} is missing its "
                "remote_reference or key_mapping."
            )
        if secret is not None and not is_external_reference(secret):
            raise ValueError(
                f"Secret metadata sync source id {source_id!r} is AWS-backed, but "
                f"{secret.name!r} is stored in Tracecat in this workspace. Delete "
                "or rename that secret first."
            )
        if SecretType(spec.secret_type or SecretType.CUSTOM.value) != SecretType.CUSTOM:
            raise ValueError(
                f"Secret metadata sync source id {source_id!r} is AWS-backed; "
                "AWS-backed secrets must use the custom type."
            )
        _require_reference_name(source_id, spec.name)
        store = await _authorized_store_named(references, stores_by_name, spec.store)
        tags = dict.fromkeys(spec.tags, "") if spec.tags else None
        if store is None and secret is not None and secret.store_id is not None:
            # Linked to a store manually in this workspace; keep that link.
            secret.name = spec.name
            secret.environment = spec.environment
            secret.tags = tags
            secret.description = spec.description
            workspace_service.session.add(secret)
            await workspace_service.session.flush()
            return secret
        if store is not None:
            try:
                references.validate_reference(store, spec.remote_reference)
            except ValueError as e:
                raise ValueError(
                    f"Secret metadata sync source id {source_id!r}: {e}"
                ) from e

        if secret is None:
            secret = Secret(
                workspace_id=workspace_service.workspace_id,
                name=spec.name,
                type=SecretType.CUSTOM.value,
                encrypted_keys=references.encrypt_keys([]),
                environment=spec.environment,
                tags=tags,
                description=spec.description,
                source=SecretSource.AWS_SECRETS_MANAGER.value,
            )
        else:
            secret.name = spec.name
            secret.environment = spec.environment
            secret.tags = tags
            secret.description = spec.description
        secret.store_id = store.id if store is not None else None
        secret.remote_reference = spec.remote_reference
        secret.remote_key_mapping = spec.key_mapping.model_dump(mode="json")
        workspace_service.session.add(secret)
        await workspace_service.session.flush()
        return secret

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


async def _authorized_store_named(
    references: SecretReferencesService,
    stores_by_name: dict[str, OrganizationSecretStore | None],
    name: str | None,
) -> OrganizationSecretStore | None:
    """Return the authorized store with ``name``, or None when there is none."""
    if name is None:
        return None
    if name not in stores_by_name:
        try:
            stores_by_name[name] = await references.get_authorized_store_by_name(name)
        except TracecatAuthorizationError:
            stores_by_name[name] = None
    return stores_by_name[name]


class _ExternalReferenceFields(TypedDict, total=False):
    source: SecretSource
    store: str
    remote_reference: str | None
    key_mapping: AwsSecretKeyMapping


def _external_reference_fields(secret: Secret) -> _ExternalReferenceFields:
    """Return the synced reference of an AWS-backed secret, never its values."""
    if not is_external_reference(secret):
        return {}
    fields: _ExternalReferenceFields = {
        "source": SecretSource.AWS_SECRETS_MANAGER,
        "remote_reference": secret.remote_reference,
        "key_mapping": AwsSecretKeyMapping.model_validate(
            secret.remote_key_mapping or {}
        ),
    }
    if secret.store is not None:
        fields["store"] = secret.store.name
    return fields


def _require_reference_name(source_id: str, name: str) -> None:
    """Reject names that AWS-backed secrets can't use."""
    if not re.fullmatch(EXPRESSION_SECRET_NAME_PATTERN, name):
        raise ValueError(
            f"Secret metadata sync source id {source_id!r} names an externally "
            f"backed secret {name!r}; AWS-backed secret names must be snake_case "
            "and start with a letter or underscore."
        )
