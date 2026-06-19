"""Workspace variable resource adapter."""

from __future__ import annotations

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
    environment_source_id,
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
        specs: dict[str, BaseModel] = {}
        resources: list[ProjectedResource] = []
        for variable in variables:
            source_id = environment_source_id(variable.environment, variable.name)
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
            variable = await ctx.session.scalar(
                select(WorkspaceVariable).where(
                    WorkspaceVariable.workspace_id == ctx.workspace_id,
                    WorkspaceVariable.name == spec.name,
                    WorkspaceVariable.environment == spec.environment,
                )
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
                variable.values = values
                variable.description = spec.description
                variable.tags = dict.fromkeys(spec.tags, "") if spec.tags else None
            ctx.session.add(variable)
            await ctx.session.flush()
            imported.append(self.imported_resource(source_id, variable.id))
        return imported


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
