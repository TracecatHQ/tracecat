"""Secret metadata resource adapter (key names only; never secret values)."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import cast

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
    ResourceProjection,
    unique_environment_source_id,
)
from tracecat.workspace_sync.enums import SyncResourceType
from tracecat.workspace_sync.schemas import (
    SECRET_METADATA_ROOT,
    SecretMetadataResourceSpec,
)


class SecretMetadataAdapter(EnvironmentYamlAdapter):
    resource_type = SyncResourceType.SECRET_METADATA
    spec_attr = "secret_metadata"
    model = SecretMetadataResourceSpec
    root = SECRET_METADATA_ROOT

    async def project(self, ctx: BaseWorkspaceService) -> ResourceProjection:
        stmt = (
            select(Secret)
            .where(Secret.workspace_id == ctx.workspace_id)
            .order_by(Secret.environment.asc(), Secret.name.asc(), Secret.id.asc())
        )
        secrets = list((await ctx.session.execute(stmt)).scalars().all())
        secret_service = SecretsService(session=ctx.session, role=ctx.role)
        source_ids_by_local_id = await self.source_ids_by_local_id(ctx)
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
            specs[source_id] = SecretMetadataResourceSpec.model_validate(
                {
                    "id": source_id,
                    "name": secret.name,
                    "environment": secret.environment,
                    "secret_type": secret.type,
                    "keys": keys,
                    "tags": sorted((secret.tags or {}).keys()),
                    "description": secret.description,
                }
            )
            resources.append(self.projected_resource(source_id, secret.id))
        return ResourceProjection(specs=specs, resources=resources)

    async def import_specs(
        self,
        ctx: BaseWorkspaceService,
        specs: Mapping[str, BaseModel],
    ) -> list[ImportedResource]:
        secret_metadata = cast(Mapping[str, SecretMetadataResourceSpec], specs)
        imported: list[ImportedResource] = []
        secret_service = SecretsService(session=ctx.session, role=ctx.role)
        for source_id, spec in sorted(secret_metadata.items()):
            secret = await self._secret_for_import(
                ctx,
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
                    workspace_id=ctx.workspace_id,
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
            ctx.session.add(secret)
            await ctx.session.flush()
            imported.append(self.imported_resource(source_id, secret.id))
        return imported

    async def _secret_for_import(
        self,
        ctx: BaseWorkspaceService,
        *,
        source_id: str,
        spec: SecretMetadataResourceSpec,
    ) -> Secret | None:
        secret = await self._secret_by_source_id(ctx, source_id=source_id)
        if secret is not None:
            await self._ensure_name_environment_available(
                ctx,
                source_id=source_id,
                name=spec.name,
                environment=spec.environment,
                secret_id=secret.id,
            )
            return secret

        return await ctx.session.scalar(
            select(Secret).where(
                Secret.workspace_id == ctx.workspace_id,
                Secret.name == spec.name,
                Secret.environment == spec.environment,
            )
        )

    async def _secret_by_source_id(
        self,
        ctx: BaseWorkspaceService,
        *,
        source_id: str,
    ) -> Secret | None:
        local_id = await self.local_id_for_source_id(ctx, source_id)
        if local_id is None:
            return None

        return await ctx.session.scalar(
            select(Secret).where(
                Secret.workspace_id == ctx.workspace_id,
                Secret.id == local_id,
            )
        )

    async def _ensure_name_environment_available(
        self,
        ctx: BaseWorkspaceService,
        *,
        source_id: str,
        name: str,
        environment: str,
        secret_id: uuid.UUID,
    ) -> None:
        conflict_id = await ctx.session.scalar(
            select(Secret.id).where(
                Secret.workspace_id == ctx.workspace_id,
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
