"""Database projectors for workspace sync resources."""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from tracecat.agent.subagents import AgentSubagentsConfig
from tracecat.db.models import (
    AgentPreset,
    AgentPresetSkill,
    CaseDropdownDefinition,
    CaseDurationDefinition,
    CaseFields,
    CaseTag,
    Secret,
    Skill,
    SkillBlob,
    SkillVersionFile,
    Table,
    WorkspaceVariable,
)
from tracecat.pagination import CursorPaginationParams
from tracecat.secrets.service import SecretsService
from tracecat.service import BaseWorkspaceService
from tracecat.storage import blob
from tracecat.tables.service import BaseTablesService
from tracecat.workspace_sync.enums import SyncResourceType
from tracecat.workspace_sync.resources import (
    agent_preset_source_path,
    case_dropdown_source_path,
    case_duration_source_path,
    case_field_source_path,
    case_tag_source_path,
    secret_metadata_source_path,
    skill_source_path,
    table_source_path,
    variable_source_path,
)
from tracecat.workspace_sync.schemas import (
    AgentPresetResourceSpec,
    AgentPresetSkillBinding,
    AgentPresetSubagentRef,
    CaseDropdownResourceSpec,
    CaseDurationResourceSpec,
    CaseFieldResourceSpec,
    CaseTagResourceSpec,
    SecretMetadataResourceSpec,
    SkillFileSpec,
    SkillResourceSpec,
    TableResourceSpec,
    VariableResourceSpec,
    WorkspaceSpec,
)


@dataclass(frozen=True, slots=True)
class ProjectedResource:
    resource_type: SyncResourceType
    source_id: str
    source_path: str
    local_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class WorkspaceResourceProjection:
    spec: WorkspaceSpec
    resources: list[ProjectedResource]


class WorkspaceResourceProjector(BaseWorkspaceService):
    """Project non-workflow workspace config resources into sync specs."""

    service_name = "workspace_resource_projector"

    async def project_non_workflow_resources(self) -> WorkspaceResourceProjection:
        resources: list[ProjectedResource] = []
        agent_presets, preset_resources = await self._project_agent_presets()
        skills, skill_resources = await self._project_skills()
        tables, table_resources = await self._project_tables()
        case_tags, tag_resources = await self._project_case_tags()
        case_dropdowns, dropdown_resources = await self._project_case_dropdowns()
        case_durations, duration_resources = await self._project_case_durations()
        case_fields, field_resources = await self._project_case_fields()
        variables, variable_resources = await self._project_variables()
        secret_metadata, secret_resources = await self._project_secret_metadata()

        resources.extend(preset_resources)
        resources.extend(skill_resources)
        resources.extend(table_resources)
        resources.extend(tag_resources)
        resources.extend(dropdown_resources)
        resources.extend(duration_resources)
        resources.extend(field_resources)
        resources.extend(variable_resources)
        resources.extend(secret_resources)

        return WorkspaceResourceProjection(
            spec=WorkspaceSpec(
                agent_presets=dict(sorted(agent_presets.items())),
                skills=dict(sorted(skills.items())),
                tables=dict(sorted(tables.items())),
                case_tags=dict(sorted(case_tags.items())),
                case_dropdowns=dict(sorted(case_dropdowns.items())),
                case_durations=dict(sorted(case_durations.items())),
                case_fields=dict(sorted(case_fields.items())),
                variables=dict(sorted(variables.items())),
                secret_metadata=dict(sorted(secret_metadata.items())),
            ),
            resources=resources,
        )

    async def _project_agent_presets(
        self,
    ) -> tuple[dict[str, AgentPresetResourceSpec], list[ProjectedResource]]:
        stmt = (
            select(AgentPreset)
            .where(AgentPreset.workspace_id == self.workspace_id)
            .options(
                selectinload(AgentPreset.folder),
                selectinload(AgentPreset.tags),
                selectinload(AgentPreset.skill_bindings).selectinload(
                    AgentPresetSkill.skill
                ),
                selectinload(AgentPreset.skill_bindings).selectinload(
                    AgentPresetSkill.skill_version
                ),
            )
            .order_by(AgentPreset.slug.asc(), AgentPreset.id.asc())
        )
        presets = list((await self.session.execute(stmt)).scalars().all())
        specs: dict[str, AgentPresetResourceSpec] = {}
        resources: list[ProjectedResource] = []
        reserved: set[str] = set()
        for preset in presets:
            source_id = _unique_source_id(preset.slug, reserved=reserved)
            reserved.add(source_id)
            skill_bindings = [
                AgentPresetSkillBinding(
                    slug=binding.skill.name,
                    version=binding.skill_version.version,
                )
                for binding in sorted(
                    preset.skill_bindings or [],
                    key=lambda item: item.skill.name,
                )
                if binding.skill is not None and binding.skill_version is not None
            ]
            spec = AgentPresetResourceSpec.model_validate(
                {
                    "id": source_id,
                    "slug": preset.slug,
                    "name": preset.name,
                    "folder_path": preset.folder.path if preset.folder else None,
                    "tags": sorted(tag.name for tag in preset.tags),
                    "instructions": preset.instructions,
                    "tool_approvals": preset.tool_approvals or {},
                    "actions": sorted(preset.actions or []),
                    "skills": skill_bindings,
                    "subagents": _subagent_refs(preset.agents),
                    "model_name": preset.model_name,
                    "model_provider": preset.model_provider,
                    "base_url": preset.base_url,
                    "output_type": preset.output_type,
                    "namespaces": sorted(preset.namespaces or []),
                    "mcp_integrations": sorted(preset.mcp_integrations or []),
                    "retries": preset.retries,
                    "enable_thinking": preset.enable_thinking,
                    "enable_internet_access": preset.enable_internet_access,
                }
            )
            specs[source_id] = spec
            resources.append(
                ProjectedResource(
                    resource_type=SyncResourceType.AGENT_PRESET,
                    source_id=source_id,
                    source_path=agent_preset_source_path(source_id),
                    local_id=preset.id,
                )
            )
        return specs, resources

    async def _project_skills(
        self,
    ) -> tuple[dict[str, SkillResourceSpec], list[ProjectedResource]]:
        stmt = (
            select(Skill)
            .where(
                Skill.workspace_id == self.workspace_id,
                Skill.archived_at.is_(None),
            )
            .options(selectinload(Skill.current_version))
            .order_by(Skill.name.asc(), Skill.id.asc())
        )
        skills = list((await self.session.execute(stmt)).scalars().all())
        specs: dict[str, SkillResourceSpec] = {}
        resources: list[ProjectedResource] = []
        reserved: set[str] = set()
        for skill in skills:
            source_id = _unique_source_id(skill.name, reserved=reserved)
            reserved.add(source_id)
            version = skill.current_version
            files: list[SkillFileSpec] = []
            file_contents: dict[str, str] = {}
            if version is not None:
                rows = await self._skill_version_rows(version.id)
                for version_file, blob_row in rows:
                    content = await blob.download_file(
                        key=blob_row.key,
                        bucket=blob_row.bucket,
                    )
                    files.append(
                        SkillFileSpec(
                            path=version_file.path,
                            sha256=blob_row.sha256,
                        )
                    )
                    file_contents[version_file.path] = content.decode("utf-8")

            spec = SkillResourceSpec.model_validate(
                {
                    "id": source_id,
                    "slug": skill.name,
                    "name": version.name if version is not None else skill.name,
                    "current_version": (
                        version.version if version is not None else None
                    ),
                    "description": skill.description,
                    "files": files,
                    "file_contents": file_contents,
                }
            )
            specs[source_id] = spec
            resources.append(
                ProjectedResource(
                    resource_type=SyncResourceType.SKILL,
                    source_id=source_id,
                    source_path=skill_source_path(source_id),
                    local_id=skill.id,
                )
            )
        return specs, resources

    async def _skill_version_rows(
        self,
        version_id: uuid.UUID,
    ) -> list[tuple[SkillVersionFile, SkillBlob]]:
        stmt = (
            select(SkillVersionFile, SkillBlob)
            .join(SkillBlob, SkillVersionFile.blob_id == SkillBlob.id)
            .where(
                SkillVersionFile.workspace_id == self.workspace_id,
                SkillVersionFile.skill_version_id == version_id,
            )
            .order_by(SkillVersionFile.path.asc())
        )
        return [
            (version_file, blob_row)
            for version_file, blob_row in (await self.session.execute(stmt)).all()
        ]

    async def _project_tables(
        self,
    ) -> tuple[dict[str, TableResourceSpec], list[ProjectedResource]]:
        stmt = (
            select(Table)
            .where(Table.workspace_id == self.workspace_id)
            .options(selectinload(Table.columns))
            .order_by(Table.name.asc(), Table.id.asc())
        )
        tables = list((await self.session.execute(stmt)).scalars().all())
        table_service = BaseTablesService(session=self.session, role=self.role)
        specs: dict[str, TableResourceSpec] = {}
        resources: list[ProjectedResource] = []
        reserved: set[str] = set()
        for table in tables:
            source_id = _unique_source_id(table.name, reserved=reserved)
            reserved.add(source_id)
            unique_columns = set(await table_service.get_index(table))
            columns: list[dict[str, Any]] = []
            for column in sorted(table.columns, key=lambda item: item.name):
                column_spec: dict[str, Any] = {
                    "name": column.name,
                    "type": column.type.lower(),
                }
                if not column.nullable:
                    column_spec["nullable"] = False
                if column.default is not None:
                    column_spec["default"] = column.default
                if column.options:
                    column_spec["options"] = column.options
                if column.name in unique_columns:
                    column_spec["unique"] = True
                columns.append(column_spec)
            rows = await self._project_table_rows(table, table_service=table_service)
            specs[source_id] = TableResourceSpec(
                id=source_id,
                name=table.name,
                columns=columns,
                rows_path="rows.jsonl",
                rows=rows,
            )
            resources.append(
                ProjectedResource(
                    resource_type=SyncResourceType.TABLE,
                    source_id=source_id,
                    source_path=table_source_path(source_id),
                    local_id=table.id,
                )
            )
        return specs, resources

    async def _project_table_rows(
        self,
        table: Table,
        *,
        table_service: BaseTablesService,
    ) -> list[dict[str, Any]]:
        cursor: str | None = None
        rows: list[dict[str, Any]] = []
        while True:
            page = await table_service.list_rows(
                table,
                CursorPaginationParams(limit=200, cursor=cursor),
                order_by="id",
                sort="asc",
            )
            for row in page.items:
                rows.append(
                    {
                        key: _jsonable(value)
                        for key, value in row.items()
                        if key not in {"id", "created_at", "updated_at"}
                    }
                )
            if not page.next_cursor:
                break
            cursor = page.next_cursor
        return sorted(rows, key=lambda row: repr(sorted(row.items())))

    async def _project_case_tags(
        self,
    ) -> tuple[dict[str, CaseTagResourceSpec], list[ProjectedResource]]:
        stmt = (
            select(CaseTag)
            .where(CaseTag.workspace_id == self.workspace_id)
            .order_by(CaseTag.ref.asc(), CaseTag.id.asc())
        )
        tags = list((await self.session.execute(stmt)).scalars().all())
        specs: dict[str, CaseTagResourceSpec] = {}
        resources: list[ProjectedResource] = []
        reserved: set[str] = set()
        for tag in tags:
            source_id = _unique_source_id(tag.ref, reserved=reserved)
            reserved.add(source_id)
            specs[source_id] = CaseTagResourceSpec(
                id=source_id,
                name=tag.name,
                color=tag.color,
            )
            resources.append(
                ProjectedResource(
                    resource_type=SyncResourceType.CASE_TAG,
                    source_id=source_id,
                    source_path=case_tag_source_path(source_id),
                    local_id=tag.id,
                )
            )
        return specs, resources

    async def _project_case_dropdowns(
        self,
    ) -> tuple[dict[str, CaseDropdownResourceSpec], list[ProjectedResource]]:
        stmt = (
            select(CaseDropdownDefinition)
            .where(CaseDropdownDefinition.workspace_id == self.workspace_id)
            .options(selectinload(CaseDropdownDefinition.options))
            .order_by(CaseDropdownDefinition.ref.asc(), CaseDropdownDefinition.id.asc())
        )
        dropdowns = list((await self.session.execute(stmt)).scalars().all())
        specs: dict[str, CaseDropdownResourceSpec] = {}
        resources: list[ProjectedResource] = []
        reserved: set[str] = set()
        for dropdown in dropdowns:
            source_id = _unique_source_id(dropdown.ref, reserved=reserved)
            reserved.add(source_id)
            specs[source_id] = CaseDropdownResourceSpec.model_validate(
                {
                    "id": source_id,
                    "name": dropdown.name,
                    "options": [
                        {
                            key: value
                            for key, value in {
                                "ref": option.ref,
                                "label": option.label,
                                "position": option.position,
                                "icon_name": option.icon_name,
                                "color": option.color,
                            }.items()
                            if value is not None
                        }
                        for option in sorted(
                            dropdown.options,
                            key=lambda item: (item.position, item.ref),
                        )
                    ],
                    "is_ordered": dropdown.is_ordered,
                    "icon_name": dropdown.icon_name,
                    "position": dropdown.position,
                    "required_on_closure": dropdown.required_on_closure,
                }
            )
            resources.append(
                ProjectedResource(
                    resource_type=SyncResourceType.CASE_DROPDOWN,
                    source_id=source_id,
                    source_path=case_dropdown_source_path(source_id),
                    local_id=dropdown.id,
                )
            )
        return specs, resources

    async def _project_case_durations(
        self,
    ) -> tuple[dict[str, CaseDurationResourceSpec], list[ProjectedResource]]:
        stmt = (
            select(CaseDurationDefinition)
            .where(CaseDurationDefinition.workspace_id == self.workspace_id)
            .order_by(
                CaseDurationDefinition.name.asc(), CaseDurationDefinition.id.asc()
            )
        )
        durations = list((await self.session.execute(stmt)).scalars().all())
        specs: dict[str, CaseDurationResourceSpec] = {}
        resources: list[ProjectedResource] = []
        reserved: set[str] = set()
        for duration in durations:
            source_id = _unique_source_id(duration.name, reserved=reserved)
            reserved.add(source_id)
            specs[source_id] = CaseDurationResourceSpec.model_validate(
                {
                    "id": source_id,
                    "name": duration.name,
                    "description": duration.description,
                    "start": {
                        "event": duration.start_event_type.value,
                        "selection": duration.start_selection.value,
                        "timestamp_path": duration.start_timestamp_path,
                        "field_filters": duration.start_field_filters,
                    },
                    "end": {
                        "event": duration.end_event_type.value,
                        "selection": duration.end_selection.value,
                        "timestamp_path": duration.end_timestamp_path,
                        "field_filters": duration.end_field_filters,
                    },
                }
            )
            resources.append(
                ProjectedResource(
                    resource_type=SyncResourceType.CASE_DURATION,
                    source_id=source_id,
                    source_path=case_duration_source_path(source_id),
                    local_id=duration.id,
                )
            )
        return specs, resources

    async def _project_case_fields(
        self,
    ) -> tuple[dict[str, CaseFieldResourceSpec], list[ProjectedResource]]:
        definition = await self.session.scalar(
            select(CaseFields).where(CaseFields.workspace_id == self.workspace_id)
        )
        if definition is None:
            return {}, []
        specs: dict[str, CaseFieldResourceSpec] = {}
        resources: list[ProjectedResource] = []
        reserved: set[str] = set()
        schema = definition.schema or {}
        for ref, field_def in sorted(schema.items()):
            if not isinstance(field_def, dict):
                continue
            source_id = _unique_source_id(ref, reserved=reserved)
            reserved.add(source_id)
            specs[source_id] = CaseFieldResourceSpec(
                id=source_id,
                name=str(field_def.get("name") or ref),
                field_type=field_def.get("type"),
                kind=field_def.get("kind"),
            )
            resources.append(
                ProjectedResource(
                    resource_type=SyncResourceType.CASE_FIELD,
                    source_id=source_id,
                    source_path=case_field_source_path(source_id),
                    local_id=definition.id,
                )
            )
        return specs, resources

    async def _project_variables(
        self,
    ) -> tuple[dict[str, VariableResourceSpec], list[ProjectedResource]]:
        stmt = (
            select(WorkspaceVariable)
            .where(WorkspaceVariable.workspace_id == self.workspace_id)
            .order_by(
                WorkspaceVariable.environment.asc(),
                WorkspaceVariable.name.asc(),
                WorkspaceVariable.id.asc(),
            )
        )
        variables = list((await self.session.execute(stmt)).scalars().all())
        specs: dict[str, VariableResourceSpec] = {}
        resources: list[ProjectedResource] = []
        for variable in variables:
            source_id = _environment_source_id(variable.environment, variable.name)
            spec = VariableResourceSpec.model_validate(
                {
                    "id": source_id,
                    "name": variable.name,
                    "environment": variable.environment,
                    "value": variable.values,
                    "description": variable.description,
                    "tags": sorted((variable.tags or {}).keys()),
                }
            )
            specs[source_id] = spec
            resources.append(
                ProjectedResource(
                    resource_type=SyncResourceType.VARIABLE,
                    source_id=source_id,
                    source_path=variable_source_path(source_id),
                    local_id=variable.id,
                )
            )
        return specs, resources

    async def _project_secret_metadata(
        self,
    ) -> tuple[dict[str, SecretMetadataResourceSpec], list[ProjectedResource]]:
        stmt = (
            select(Secret)
            .where(Secret.workspace_id == self.workspace_id)
            .order_by(Secret.environment.asc(), Secret.name.asc(), Secret.id.asc())
        )
        secrets = list((await self.session.execute(stmt)).scalars().all())
        secret_service = SecretsService(session=self.session, role=self.role)
        specs: dict[str, SecretMetadataResourceSpec] = {}
        resources: list[ProjectedResource] = []
        for secret in secrets:
            source_id = _environment_source_id(secret.environment, secret.name)
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
            resources.append(
                ProjectedResource(
                    resource_type=SyncResourceType.SECRET_METADATA,
                    source_id=source_id,
                    source_path=secret_metadata_source_path(source_id),
                    local_id=secret.id,
                )
            )
        return specs, resources


def _subagent_refs(agents: dict[str, Any]) -> list[AgentPresetSubagentRef]:
    try:
        config = AgentSubagentsConfig.model_validate(agents or {"enabled": False})
    except Exception:
        return []
    return [
        AgentPresetSubagentRef(slug=subagent.preset)
        for subagent in sorted(config.subagents, key=lambda item: item.preset)
    ]


def _environment_source_id(environment: str, name: str) -> str:
    return f"{_path_segment(environment, fallback='default')}/{_path_segment(name)}"


def _unique_source_id(value: str, *, reserved: set[str]) -> str:
    base = _path_segment(value)
    candidate = base
    counter = 2
    while candidate in reserved:
        candidate = f"{base}-{counter}"
        counter += 1
    return candidate


def _path_segment(value: str, *, fallback: str = "resource") -> str:
    cleaned = value.strip().replace("/", "-").replace("\\", "-")
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", cleaned).strip("-._")
    return safe[:96].strip("-._") or fallback


def _jsonable(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_jsonable(item) for item in value]
    return value
