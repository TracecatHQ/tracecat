"""Case field definition resource adapter."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from tracecat.cases.enums import CaseFieldKind
from tracecat.cases.schemas import CaseFieldCreate, CaseFieldUpdate
from tracecat.cases.service import CaseFieldsService
from tracecat.db.models import CaseFields
from tracecat.tables.enums import SqlType
from tracecat.workspace_sync.adapters.base import (
    FlatManifestAdapter,
    ImportedResource,
    ProjectedResource,
    ResourceProjection,
    SyncMappingService,
    find_duplicates,
    sql_type,
)
from tracecat.workspace_sync.enums import SyncResourceType
from tracecat.workspace_sync.schemas import (
    CASE_FIELD_ROOT,
    CaseFieldResourceSpec,
    WorkspaceSpec,
)


class CaseFieldAdapter(FlatManifestAdapter):
    """Sync adapter for case field definitions held in the case fields schema.

    Fields live as entries in a single workspace-wide :class:`CaseFields` schema
    rather than as their own rows, so each spec maps to one schema key.
    """

    resource_type = SyncResourceType.CASE_FIELD
    spec_attr = "case_fields"
    model = CaseFieldResourceSpec
    read_scope = "case:read"
    create_scope = "case:create"
    update_scope = "case:update"
    root = CASE_FIELD_ROOT
    import_identity_attrs = ("name",)
    import_identity_noun = "name"

    async def project(
        self, workspace_service: SyncMappingService
    ) -> ResourceProjection:
        """Project each entry of the workspace case fields schema into a spec."""
        # All case fields live in one workspace-wide schema row; absence means
        # there are no fields to project.
        definition = await workspace_service.session.scalar(
            select(CaseFields).where(
                CaseFields.workspace_id == workspace_service.workspace_id
            )
        )
        if definition is None:
            return ResourceProjection(specs={}, resources=[])
        specs: dict[str, BaseModel] = {}
        resources: list[ProjectedResource] = []
        assigner = await self.source_id_assigner(workspace_service)
        schema = definition.schema or {}
        # Each schema key is one field; sort for deterministic source-id minting.
        for ref, field_def in sorted(schema.items()):
            if not isinstance(field_def, dict):
                continue
            local_id = _case_field_local_id(definition.id, ref)
            source_id = assigner.assign(local_id, ref)
            name = str(field_def.get("name") or ref)
            with self.projection_error_context(
                source_id=source_id,
                display_name=name,
                local_id=local_id,
            ):
                specs[source_id] = CaseFieldResourceSpec(
                    id=source_id,
                    name=name,
                    field_type=field_def.get("type"),
                    kind=field_def.get("kind"),
                    options=field_def.get("options"),
                    required_on_closure=bool(field_def.get("required_on_closure")),
                )
                # Fields have no row of their own, so derive a stable local id from
                # the schema row id plus field ref.
                resources.append(self.projected_resource(source_id, local_id))
        return ResourceProjection(specs=specs, resources=resources)

    async def import_specs(
        self,
        workspace_service: SyncMappingService,
        workspace_spec: WorkspaceSpec,
    ) -> list[ImportedResource]:
        """Reconcile field specs, creating new columns or updating schema entries."""
        fields = workspace_spec.case_fields
        imported: list[ImportedResource] = []
        field_service = CaseFieldsService(
            session=workspace_service.session, role=workspace_service.role
        )
        await self._release_changing_mapped_fields(
            workspace_service,
            field_service=field_service,
            fields=fields,
        )
        # Sort for deterministic processing; re-fetch the schema per field below
        # because create_column mutates the shared CaseFields row as we go.
        for source_id, spec in sorted(fields.items()):
            definition = await workspace_service.session.scalar(
                select(CaseFields).where(
                    CaseFields.workspace_id == workspace_service.workspace_id
                )
            )
            current_schema = dict(definition.schema or {}) if definition else {}
            field_id = await self._field_id_for_import(
                workspace_service,
                definition=definition,
                current_schema=current_schema,
                source_id=source_id,
                spec=spec,
            )
            field_type = sql_type(spec.field_type or "text")
            field_kind = spec.kind
            field_options = _case_field_options(spec, current_schema, field_type)
            # New field: provision an actual table column, not just a schema entry.
            if field_id not in current_schema:
                field_params = CaseFieldCreate(
                    name=spec.name,
                    type=field_type,
                    kind=_case_field_kind(field_kind),
                    options=field_options,
                )
                # Force nullable so adding a column to populated cases never fails.
                field_params.nullable = True
                await field_service._ensure_schema_ready()
                await field_service.editor.create_column(field_params)
                # Mirror the new column into the schema map, copying only the
                # parts that are present so the entry stays minimal.
                field_def: dict[str, Any] = {"type": field_params.type.value}
                if field_params.options:
                    field_def["options"] = field_params.options
                if field_params.kind is not None:
                    field_def["kind"] = field_params.kind.value
                if spec.required_on_closure:
                    field_def["required_on_closure"] = True
                await field_service._update_field_schema(field_params.name, field_def)
            else:
                # Existing field: use the service update path so physical table
                # columns, schema metadata, and sync mappings move together.
                await field_service.update_field(
                    field_id,
                    CaseFieldUpdate(
                        name=spec.name if spec.name != field_id else None,
                        type=field_type,
                        options=field_options,
                        required_on_closure=spec.required_on_closure,
                    ),
                    commit=False,
                )
                kind = _case_field_kind(field_kind)
                definition = await workspace_service.session.scalar(
                    select(CaseFields).where(
                        CaseFields.workspace_id == workspace_service.workspace_id
                    )
                )
                if definition is not None:
                    updated_schema = dict(definition.schema or {})
                    updated_field_def = dict(updated_schema.get(spec.name, {}))
                    if kind is None:
                        updated_field_def.pop("kind", None)
                    else:
                        updated_field_def["kind"] = kind.value
                    updated_schema[spec.name] = updated_field_def
                    definition.schema = updated_schema
                    flag_modified(definition, "schema")
                    workspace_service.session.add(definition)
                    await workspace_service.session.flush()
            # Re-fetch to get the committed row (and its id) after either branch
            # may have created or mutated the definition.
            definition = await workspace_service.session.scalar(
                select(CaseFields).where(
                    CaseFields.workspace_id == workspace_service.workspace_id
                )
            )
            if definition is not None:
                imported.append(
                    self.imported_resource(
                        source_id,
                        _case_field_local_id(definition.id, spec.name),
                    )
                )
        return imported

    async def _field_id_for_import(
        self,
        workspace_service: SyncMappingService,
        *,
        definition: CaseFields | None,
        current_schema: Mapping[str, Any],
        source_id: str,
        spec: CaseFieldResourceSpec,
    ) -> str:
        """Resolve the local schema key a source id should update."""
        if definition is not None:
            mapped_local_id = await self.local_id_for_source_id(
                workspace_service,
                source_id,
            )
            if mapped_local_id is not None:
                for field_id in sorted(current_schema):
                    if _case_field_local_id(definition.id, field_id) == mapped_local_id:
                        return field_id
        if source_id in current_schema:
            return source_id
        return spec.name

    async def _release_changing_mapped_fields(
        self,
        workspace_service: SyncMappingService,
        *,
        field_service: CaseFieldsService,
        fields: Mapping[str, CaseFieldResourceSpec],
    ) -> None:
        """Park mapped existing fields whose names change in this batch."""
        if not fields:
            return
        duplicate_names = find_duplicates(spec.name for spec in fields.values())
        if duplicate_names:
            raise ValueError(
                "Case field sync specs must have unique names: "
                + ", ".join(repr(name) for name in duplicate_names)
            )

        definition = await workspace_service.session.scalar(
            select(CaseFields).where(
                CaseFields.workspace_id == workspace_service.workspace_id
            )
        )
        if definition is None:
            return

        current_schema = dict(definition.schema or {})
        existing_field_ids_by_source_id: dict[str, str] = {}
        for source_id, spec in sorted(fields.items()):
            field_id = await self._field_id_for_import(
                workspace_service,
                definition=definition,
                current_schema=current_schema,
                source_id=source_id,
                spec=spec,
            )
            if field_id in current_schema:
                existing_field_ids_by_source_id[source_id] = field_id

        source_ids_by_field_id = {
            field_id: source_id
            for source_id, field_id in existing_field_ids_by_source_id.items()
        }
        for source_id, field_id in existing_field_ids_by_source_id.items():
            target_name = fields[source_id].name
            if target_name == field_id or target_name not in current_schema:
                continue
            owner_source_id = source_ids_by_field_id.get(target_name)
            if (
                owner_source_id is not None
                and fields[owner_source_id].name != target_name
            ):
                continue
            raise ValueError(
                f"Case field sync source id {source_id!r} cannot use name "
                f"{target_name!r} because another field already uses that name."
            )

        reserved_names = set(current_schema) | {spec.name for spec in fields.values()}
        for source_id, field_id in existing_field_ids_by_source_id.items():
            if field_id == fields[source_id].name:
                continue
            temp_name = _unique_temporary_case_field_name(
                definition.id,
                field_id,
                reserved_names,
            )
            await field_service.update_field(
                field_id,
                CaseFieldUpdate(name=temp_name),
                commit=False,
            )


def _case_field_kind(value: Any) -> CaseFieldKind | None:
    """Coerce a raw ``kind`` value into a :class:`CaseFieldKind`, or ``None``."""
    if value is None:
        return None
    try:
        return CaseFieldKind(str(value))
    except ValueError:
        # Unknown kind from external YAML: treat as absent rather than failing.
        return None


def _unique_temporary_case_field_name(
    definition_id: uuid.UUID,
    field_id: str,
    reserved_names: set[str],
) -> str:
    """Return a temporary case field name that does not collide."""
    field_token = uuid.uuid5(definition_id, field_id).hex[:16]
    base = f"tracecat_sync_tmp_{field_token}"
    candidate = base
    suffix = 1
    while candidate in reserved_names:
        suffix += 1
        candidate = f"{base}_{suffix}"
    reserved_names.add(candidate)
    return candidate


def _case_field_options(
    spec: CaseFieldResourceSpec,
    current_schema: Mapping[str, Any],
    field_type: SqlType,
) -> list[str] | None:
    """Resolve select options for a field, falling back to the current schema.

    Returns ``None`` for non-select field types or when no options are
    available. Prefers ``spec.options``, else the options already stored in
    ``current_schema``.
    """
    # Only select-style fields carry options; everything else has none.
    if field_type not in (SqlType.SELECT, SqlType.MULTI_SELECT):
        return None
    # An explicit spec value always wins over what is already stored.
    if spec.options is not None:
        return spec.options
    # Fall back to the existing schema entry so a partial spec keeps its options.
    field_def = current_schema.get(spec.id)
    if isinstance(field_def, Mapping):
        options = field_def.get("options")
        if isinstance(options, list):
            # Normalize stored values to str in case the schema holds non-strings.
            return [str(option) for option in options]
    return None


def _case_field_local_id(definition_id: uuid.UUID, source_id: str) -> uuid.UUID:
    """Derive a stable per-field ``local_id`` from the schema definition and source id.

    Case fields share one :class:`CaseFields` row, so a UUIDv5 of the definition
    id and ``source_id`` gives each field a deterministic, distinct local id.
    """
    # UUIDv5 is deterministic, so the same field always maps to the same id.
    return uuid.uuid5(definition_id, source_id)
