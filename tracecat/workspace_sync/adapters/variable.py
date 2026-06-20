"""Workspace variable resource adapter."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any, cast

from pydantic import BaseModel
from sqlalchemy import select

from tracecat.db.models import WorkspaceVariable
from tracecat.service import BaseWorkspaceService
from tracecat.workspace_sync.adapters.base import (
    EnvironmentYamlAdapter,
    ImportedResource,
    ProjectedResource,
    ResourceProjection,
    unique_environment_source_id,
)
from tracecat.workspace_sync.enums import SyncResourceType
from tracecat.workspace_sync.schemas import VARIABLE_ROOT, VariableResourceSpec


class VariableAdapter(EnvironmentYamlAdapter):
    resource_type = SyncResourceType.VARIABLE
    spec_attr = "variables"
    model = VariableResourceSpec
    root = VARIABLE_ROOT

    async def project(self, ctx: BaseWorkspaceService) -> ResourceProjection:
        stmt = (
            select(WorkspaceVariable)
            .where(WorkspaceVariable.workspace_id == ctx.workspace_id)
            .order_by(
                WorkspaceVariable.environment.asc(),
                WorkspaceVariable.name.asc(),
                WorkspaceVariable.id.asc(),
            )
        )
        variables = list((await ctx.session.execute(stmt)).scalars().all())
        source_ids_by_local_id = await self.source_ids_by_local_id(ctx)
        specs: dict[str, BaseModel] = {}
        resources: list[ProjectedResource] = []
        reserved: set[str] = set(source_ids_by_local_id.values())
        for variable in variables:
            source_id = source_ids_by_local_id.get(variable.id)
            if source_id is None:
                source_id = unique_environment_source_id(
                    variable.environment,
                    variable.name,
                    reserved=reserved,
                )
            reserved.add(source_id)
            specs[source_id] = VariableResourceSpec.model_validate(
                {
                    "id": source_id,
                    "name": variable.name,
                    "environment": variable.environment,
                    "keys": sorted((variable.values or {}).keys()),
                    "description": variable.description,
                    "tags": sorted((variable.tags or {}).keys()),
                }
            )
            resources.append(self.projected_resource(source_id, variable.id))
        return ResourceProjection(specs=specs, resources=resources)

    async def import_specs(
        self,
        ctx: BaseWorkspaceService,
        specs: Mapping[str, BaseModel],
    ) -> list[ImportedResource]:
        variables = cast(Mapping[str, VariableResourceSpec], specs)
        imported: list[ImportedResource] = []
        for source_id, spec in sorted(variables.items()):
            variable = await self._variable_for_import(
                ctx,
                source_id=source_id,
                spec=spec,
            )
            values = _values_from_spec(
                spec, existing=variable.values if variable else {}
            )
            if variable is None:
                variable = WorkspaceVariable(
                    workspace_id=ctx.workspace_id,
                    name=spec.name,
                    environment=spec.environment,
                    values=values,
                    description=spec.description,
                    tags=dict.fromkeys(spec.tags, "") if spec.tags else None,
                )
            else:
                variable.name = spec.name
                variable.environment = spec.environment
                variable.values = values
                variable.description = spec.description
                variable.tags = dict.fromkeys(spec.tags, "") if spec.tags else None
            ctx.session.add(variable)
            await ctx.session.flush()
            imported.append(self.imported_resource(source_id, variable.id))
        return imported

    async def _variable_for_import(
        self,
        ctx: BaseWorkspaceService,
        *,
        source_id: str,
        spec: VariableResourceSpec,
    ) -> WorkspaceVariable | None:
        variable = await self._variable_by_source_id(ctx, source_id=source_id)
        if variable is not None:
            await self._ensure_name_environment_available(
                ctx,
                source_id=source_id,
                name=spec.name,
                environment=spec.environment,
                variable_id=variable.id,
            )
            return variable

        return await ctx.session.scalar(
            select(WorkspaceVariable).where(
                WorkspaceVariable.workspace_id == ctx.workspace_id,
                WorkspaceVariable.name == spec.name,
                WorkspaceVariable.environment == spec.environment,
            )
        )

    async def _variable_by_source_id(
        self,
        ctx: BaseWorkspaceService,
        *,
        source_id: str,
    ) -> WorkspaceVariable | None:
        local_id = await self.local_id_for_source_id(ctx, source_id)
        if local_id is None:
            return None

        return await ctx.session.scalar(
            select(WorkspaceVariable).where(
                WorkspaceVariable.workspace_id == ctx.workspace_id,
                WorkspaceVariable.id == local_id,
            )
        )

    async def _ensure_name_environment_available(
        self,
        ctx: BaseWorkspaceService,
        *,
        source_id: str,
        name: str,
        environment: str,
        variable_id: uuid.UUID,
    ) -> None:
        conflict_id = await ctx.session.scalar(
            select(WorkspaceVariable.id).where(
                WorkspaceVariable.workspace_id == ctx.workspace_id,
                WorkspaceVariable.name == name,
                WorkspaceVariable.environment == environment,
                WorkspaceVariable.id != variable_id,
            )
        )
        if conflict_id is None:
            return

        raise ValueError(
            f"Variable sync source id {source_id!r} cannot use "
            f"{environment!r}/{name!r} because another variable already uses it."
        )


def _values_from_spec(
    spec: VariableResourceSpec,
    *,
    existing: dict[str, Any] | None,
) -> dict[str, Any]:
    if spec.value is not None:
        return spec.value if isinstance(spec.value, dict) else {"value": spec.value}
    existing_values = existing or {}
    if spec.keys is None:
        return dict(existing_values)
    return {key: existing_values.get(key, "") for key in spec.keys}
