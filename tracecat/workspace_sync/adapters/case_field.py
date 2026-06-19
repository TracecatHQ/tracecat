"""Case field definition resource adapter."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from tracecat.cases.enums import CaseFieldKind
from tracecat.cases.schemas import CaseFieldCreate
from tracecat.cases.service import CaseFieldsService
from tracecat.db.models import CaseFields
from tracecat.service import BaseWorkspaceService
from tracecat.tables.enums import SqlType
from tracecat.workspace_sync.adapters.base import (
    ImportedResource,
    ProjectedResource,
    ResourceProjection,
    SingleYamlAdapter,
    sql_type,
    unique_source_id,
)
from tracecat.workspace_sync.enums import SyncResourceType
from tracecat.workspace_sync.schemas import CASE_FIELD_ROOT, CaseFieldResourceSpec


class CaseFieldAdapter(SingleYamlAdapter):
    resource_type = SyncResourceType.CASE_FIELD
    spec_attr = "case_fields"
    model = CaseFieldResourceSpec
    root = CASE_FIELD_ROOT

    async def project(self, ctx: BaseWorkspaceService) -> ResourceProjection:
        definition = await ctx.session.scalar(
            select(CaseFields).where(CaseFields.workspace_id == ctx.workspace_id)
        )
        if definition is None:
            return ResourceProjection(specs={}, resources=[])
        specs: dict[str, BaseModel] = {}
        resources: list[ProjectedResource] = []
        reserved: set[str] = set()
        schema = definition.schema or {}
        for ref, field_def in sorted(schema.items()):
            if not isinstance(field_def, dict):
                continue
            source_id = unique_source_id(ref, reserved=reserved)
            reserved.add(source_id)
            specs[source_id] = CaseFieldResourceSpec(
                id=source_id,
                name=str(field_def.get("name") or ref),
                field_type=field_def.get("type"),
                kind=field_def.get("kind"),
                options=field_def.get("options"),
            )
            resources.append(self.projected_resource(source_id, definition.id))
        return ResourceProjection(specs=specs, resources=resources)

    async def import_specs(
        self,
        ctx: BaseWorkspaceService,
        specs: Mapping[str, BaseModel],
    ) -> list[ImportedResource]:
        fields = cast(Mapping[str, CaseFieldResourceSpec], specs)
        imported: list[ImportedResource] = []
        field_service = CaseFieldsService(session=ctx.session, role=ctx.role)
        for source_id, spec in sorted(fields.items()):
            definition = await ctx.session.scalar(
                select(CaseFields).where(CaseFields.workspace_id == ctx.workspace_id)
            )
            current_schema = dict(definition.schema or {}) if definition else {}
            field_type = sql_type(spec.field_type or "text")
            field_kind = spec.kind
            field_options = _case_field_options(spec, current_schema, field_type)
            if source_id not in current_schema:
                field_params = CaseFieldCreate(
                    name=spec.name,
                    type=field_type,
                    kind=_case_field_kind(field_kind),
                    options=field_options,
                )
                field_params.nullable = True
                await field_service._ensure_schema_ready()
                await field_service.editor.create_column(field_params)
                field_def: dict[str, Any] = {"type": field_params.type.value}
                if field_params.options:
                    field_def["options"] = field_params.options
                if field_params.kind is not None:
                    field_def["kind"] = field_params.kind.value
                await field_service._update_field_schema(field_params.name, field_def)
            else:
                current_schema[source_id] = {
                    "type": field_type.value,
                    **({"options": field_options} if field_options else {}),
                    **({"kind": field_kind} if field_kind else {}),
                }
                if definition is None:
                    definition = CaseFields(
                        workspace_id=ctx.workspace_id,
                        schema={},
                    )
                definition.schema = current_schema
                flag_modified(definition, "schema")
                ctx.session.add(definition)
                await ctx.session.flush()
            definition = await ctx.session.scalar(
                select(CaseFields).where(CaseFields.workspace_id == ctx.workspace_id)
            )
            if definition is not None:
                imported.append(self.imported_resource(source_id, definition.id))
        return imported


def _case_field_kind(value: Any) -> CaseFieldKind | None:
    if value is None:
        return None
    try:
        return CaseFieldKind(str(value))
    except ValueError:
        return None


def _case_field_options(
    spec: CaseFieldResourceSpec,
    current_schema: Mapping[str, Any],
    field_type: SqlType,
) -> list[str] | None:
    if field_type not in (SqlType.SELECT, SqlType.MULTI_SELECT):
        return None
    if spec.options is not None:
        return spec.options
    field_def = current_schema.get(spec.id)
    if isinstance(field_def, Mapping):
        options = field_def.get("options")
        if isinstance(options, list):
            return [str(option) for option in options]
    return None
