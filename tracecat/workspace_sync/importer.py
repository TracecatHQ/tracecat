"""Database reconcilers for workspace sync resources."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import orjson
import sqlalchemy as sa
from pydantic import SecretStr
from slugify import slugify
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import flag_modified

from tracecat.agent.skill.service import SkillFileBlobRef, SkillService
from tracecat.agent.subagents import AgentSubagentsConfig
from tracecat.cases.durations.schemas import CaseDurationAnchorSelection
from tracecat.cases.enums import CaseEventType, CaseFieldKind
from tracecat.cases.schemas import CaseFieldCreate
from tracecat.cases.service import CaseFieldsService
from tracecat.db.models import (
    AgentFolder,
    AgentPreset,
    AgentPresetSkill,
    AgentPresetVersion,
    AgentPresetVersionSkill,
    AgentTag,
    AgentTagLink,
    CaseDropdownDefinition,
    CaseDropdownOption,
    CaseDurationDefinition,
    CaseFields,
    CaseTag,
    Secret,
    Skill,
    SkillVersion,
    SkillVersionFile,
    WorkspaceVariable,
)
from tracecat.exceptions import TracecatNotFoundError
from tracecat.secrets.enums import SecretType
from tracecat.secrets.schemas import SecretKeyValue
from tracecat.secrets.service import SecretsService
from tracecat.service import BaseWorkspaceService
from tracecat.tables.enums import SqlType
from tracecat.tables.schemas import TableColumnCreate, TableCreate, TableRowInsert
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
    CaseDropdownResourceSpec,
    CaseDurationResourceSpec,
    CaseFieldResourceSpec,
    CaseTagResourceSpec,
    SecretMetadataResourceSpec,
    SkillResourceSpec,
    TableResourceSpec,
    VariableResourceSpec,
    WorkspaceSpec,
)

DEFAULT_AGENT_MODEL_NAME = "gpt-4.1-mini"
DEFAULT_AGENT_MODEL_PROVIDER = "openai"


@dataclass(frozen=True, slots=True)
class ImportedResource:
    resource_type: SyncResourceType
    source_id: str
    source_path: str
    local_id: uuid.UUID


class WorkspaceResourceImportService(BaseWorkspaceService):
    """Reconcile non-workflow workspace sync resource specs into the DB."""

    service_name = "workspace_resource_import"

    async def import_non_workflow_resources(
        self,
        spec: WorkspaceSpec,
    ) -> list[ImportedResource]:
        imported: list[ImportedResource] = []
        imported.extend(await self._import_variables(spec.variables))
        imported.extend(await self._import_secret_metadata(spec.secret_metadata))
        imported.extend(await self._import_case_tags(spec.case_tags))
        imported.extend(await self._import_case_dropdowns(spec.case_dropdowns))
        imported.extend(await self._import_case_durations(spec.case_durations))
        imported.extend(await self._import_case_fields(spec.case_fields))
        imported.extend(await self._import_tables(spec.tables))
        imported.extend(await self._import_skills(spec.skills))
        imported.extend(await self._import_agent_presets(spec.agent_presets))
        return imported

    async def _import_variables(
        self,
        variables: dict[str, VariableResourceSpec],
    ) -> list[ImportedResource]:
        imported: list[ImportedResource] = []
        for source_id, spec in sorted(variables.items()):
            variable = await self.session.scalar(
                select(WorkspaceVariable).where(
                    WorkspaceVariable.workspace_id == self.workspace_id,
                    WorkspaceVariable.name == spec.name,
                    WorkspaceVariable.environment == spec.environment,
                )
            )
            if variable is None:
                variable = WorkspaceVariable(
                    workspace_id=self.workspace_id,
                    name=spec.name,
                    environment=spec.environment,
                    values=spec.value
                    if isinstance(spec.value, dict)
                    else {"value": spec.value},
                )
            else:
                variable.values = (
                    spec.value
                    if isinstance(spec.value, dict)
                    else {"value": spec.value}
                )
            self.session.add(variable)
            await self.session.flush()
            imported.append(
                ImportedResource(
                    resource_type=SyncResourceType.VARIABLE,
                    source_id=source_id,
                    source_path=variable_source_path(source_id),
                    local_id=variable.id,
                )
            )
        return imported

    async def _import_secret_metadata(
        self,
        secret_metadata: dict[str, SecretMetadataResourceSpec],
    ) -> list[ImportedResource]:
        imported: list[ImportedResource] = []
        secret_service = SecretsService(session=self.session, role=self.role)
        for source_id, spec in sorted(secret_metadata.items()):
            secret = await self.session.scalar(
                select(Secret).where(
                    Secret.workspace_id == self.workspace_id,
                    Secret.name == spec.name,
                    Secret.environment == spec.environment,
                )
            )
            existing_values: dict[str, SecretStr] = {}
            if secret is not None:
                try:
                    existing_values = {
                        key_value.key: key_value.value
                        for key_value in secret_service.decrypt_keys(
                            secret.encrypted_keys
                        )
                    }
                except Exception:
                    existing_values = {}

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
                    workspace_id=self.workspace_id,
                    name=spec.name,
                    type=secret_type,
                    encrypted_keys=encrypted_keys,
                    environment=spec.environment,
                    tags=tags,
                )
            else:
                secret.type = secret_type
                secret.encrypted_keys = encrypted_keys
                secret.tags = tags
            self.session.add(secret)
            await self.session.flush()
            imported.append(
                ImportedResource(
                    resource_type=SyncResourceType.SECRET_METADATA,
                    source_id=source_id,
                    source_path=secret_metadata_source_path(source_id),
                    local_id=secret.id,
                )
            )
        return imported

    async def _import_case_tags(
        self,
        tags: dict[str, CaseTagResourceSpec],
    ) -> list[ImportedResource]:
        imported: list[ImportedResource] = []
        for source_id, spec in sorted(tags.items()):
            tag = await self.session.scalar(
                select(CaseTag).where(
                    CaseTag.workspace_id == self.workspace_id,
                    CaseTag.ref == source_id,
                )
            )
            if tag is None:
                tag = CaseTag(
                    workspace_id=self.workspace_id,
                    name=spec.name,
                    ref=source_id,
                    color=spec.color,
                )
            else:
                tag.name = spec.name
                tag.color = spec.color
            self.session.add(tag)
            await self.session.flush()
            imported.append(
                ImportedResource(
                    resource_type=SyncResourceType.CASE_TAG,
                    source_id=source_id,
                    source_path=case_tag_source_path(source_id),
                    local_id=tag.id,
                )
            )
        return imported

    async def _import_case_dropdowns(
        self,
        dropdowns: dict[str, CaseDropdownResourceSpec],
    ) -> list[ImportedResource]:
        imported: list[ImportedResource] = []
        for source_id, spec in sorted(dropdowns.items()):
            dropdown = await self.session.scalar(
                select(CaseDropdownDefinition)
                .where(
                    CaseDropdownDefinition.workspace_id == self.workspace_id,
                    CaseDropdownDefinition.ref == source_id,
                )
                .options(selectinload(CaseDropdownDefinition.options))
            )
            if dropdown is None:
                dropdown = CaseDropdownDefinition(
                    workspace_id=self.workspace_id,
                    name=spec.name,
                    ref=source_id,
                    is_ordered=True,
                    position=0,
                )
                self.session.add(dropdown)
                await self.session.flush()
                existing_options = {}
            else:
                dropdown.name = spec.name
                dropdown.is_ordered = True
                existing_options = {option.ref: option for option in dropdown.options}

            desired_refs = set()
            for position, option_spec in enumerate(spec.options):
                ref = str(
                    option_spec.get("ref") or option_spec.get("label") or position
                )
                desired_refs.add(ref)
                option = existing_options.get(ref)
                if option is None:
                    option = CaseDropdownOption(
                        definition_id=dropdown.id,
                        ref=ref,
                        label=str(option_spec.get("label") or ref),
                    )
                option.label = str(option_spec.get("label") or ref)
                option.position = int(option_spec.get("position", position))
                option.icon_name = option_spec.get("icon_name")
                option.color = option_spec.get("color")
                self.session.add(option)
            for option in existing_options.values():
                if option.ref not in desired_refs:
                    await self.session.delete(option)
            self.session.add(dropdown)
            await self.session.flush()
            imported.append(
                ImportedResource(
                    resource_type=SyncResourceType.CASE_DROPDOWN,
                    source_id=source_id,
                    source_path=case_dropdown_source_path(source_id),
                    local_id=dropdown.id,
                )
            )
        return imported

    async def _import_case_durations(
        self,
        durations: dict[str, CaseDurationResourceSpec],
    ) -> list[ImportedResource]:
        imported: list[ImportedResource] = []
        for source_id, spec in sorted(durations.items()):
            duration = await self.session.scalar(
                select(CaseDurationDefinition).where(
                    CaseDurationDefinition.workspace_id == self.workspace_id,
                    CaseDurationDefinition.name == spec.name,
                )
            )
            start = _duration_anchor(spec, "start")
            end = _duration_anchor(spec, "end")
            attrs = {
                "name": spec.name,
                "description": getattr(spec, "description", None),
                "start_event_type": start["event_type"],
                "start_selection": start["selection"],
                "start_timestamp_path": start["timestamp_path"],
                "start_field_filters": start["field_filters"],
                "end_event_type": end["event_type"],
                "end_selection": end["selection"],
                "end_timestamp_path": end["timestamp_path"],
                "end_field_filters": end["field_filters"],
            }
            if duration is None:
                duration = CaseDurationDefinition(
                    workspace_id=self.workspace_id,
                    **attrs,
                )
            else:
                for key, value in attrs.items():
                    setattr(duration, key, value)
            self.session.add(duration)
            await self.session.flush()
            imported.append(
                ImportedResource(
                    resource_type=SyncResourceType.CASE_DURATION,
                    source_id=source_id,
                    source_path=case_duration_source_path(source_id),
                    local_id=duration.id,
                )
            )
        return imported

    async def _import_case_fields(
        self,
        fields: dict[str, CaseFieldResourceSpec],
    ) -> list[ImportedResource]:
        imported: list[ImportedResource] = []
        field_service = CaseFieldsService(session=self.session, role=self.role)
        for source_id, spec in sorted(fields.items()):
            definition = await self.session.scalar(
                select(CaseFields).where(CaseFields.workspace_id == self.workspace_id)
            )
            current_schema = dict(definition.schema or {}) if definition else {}
            field_type = _sql_type(spec.field_type or "text")
            field_kind = spec.kind
            if source_id not in current_schema:
                field_params = CaseFieldCreate(
                    name=spec.name,
                    type=field_type,
                    kind=_case_field_kind(field_kind),
                )
                field_params.nullable = True
                await field_service._ensure_schema_ready()
                await field_service.editor.create_column(field_params)
                field_def: dict[str, Any] = {"type": field_params.type.value}
                if field_params.kind is not None:
                    field_def["kind"] = field_params.kind.value
                await field_service._update_field_schema(field_params.name, field_def)
            else:
                current_schema[source_id] = {
                    "type": field_type.value,
                    **({"kind": field_kind} if field_kind else {}),
                }
                if definition is None:
                    definition = CaseFields(
                        workspace_id=self.workspace_id,
                        schema={},
                    )
                definition.schema = current_schema
                flag_modified(definition, "schema")
                self.session.add(definition)
                await self.session.flush()
            definition = await self.session.scalar(
                select(CaseFields).where(CaseFields.workspace_id == self.workspace_id)
            )
            if definition is not None:
                imported.append(
                    ImportedResource(
                        resource_type=SyncResourceType.CASE_FIELD,
                        source_id=source_id,
                        source_path=case_field_source_path(source_id),
                        local_id=definition.id,
                    )
                )
        return imported

    async def _import_tables(
        self,
        tables: dict[str, TableResourceSpec],
    ) -> list[ImportedResource]:
        imported: list[ImportedResource] = []
        table_service = BaseTablesService(session=self.session, role=self.role)
        for source_id, spec in sorted(tables.items()):
            try:
                table = await table_service.get_table_by_name(spec.name)
            except TracecatNotFoundError:
                table = await table_service.create_table(
                    TableCreate(
                        name=spec.name,
                        columns=[
                            TableColumnCreate(
                                name=str(column["name"]),
                                type=_sql_type(column["type"]),
                                nullable=bool(column.get("nullable", True)),
                                default=column.get("default"),
                                options=column.get("options"),
                            )
                            for column in spec.columns
                        ],
                    )
                )
                await self.session.refresh(table, ["columns"])
            else:
                await self.session.refresh(table, ["columns"])
                existing_columns = {column.name for column in table.columns}
                for column in spec.columns:
                    column_name = str(column["name"])
                    if column_name in existing_columns:
                        continue
                    await table_service.create_column(
                        table,
                        TableColumnCreate(
                            name=column_name,
                            type=_sql_type(column["type"]),
                            nullable=bool(column.get("nullable", True)),
                            default=column.get("default"),
                            options=column.get("options"),
                        ),
                    )
                await self.session.refresh(table, ["columns"])

            for column in spec.columns:
                if not column.get("unique"):
                    continue
                try:
                    if not await table_service.get_index(table):
                        await table_service.create_unique_index(
                            table, str(column["name"])
                        )
                except ValueError:
                    pass

            for row in spec.rows:
                await table_service.insert_row(
                    table, TableRowInsert(data=row, upsert=True)
                )
            await self.session.flush()
            imported.append(
                ImportedResource(
                    resource_type=SyncResourceType.TABLE,
                    source_id=source_id,
                    source_path=table_source_path(source_id),
                    local_id=table.id,
                )
            )
        return imported

    async def _import_skills(
        self,
        skills: dict[str, SkillResourceSpec],
    ) -> list[ImportedResource]:
        imported: list[ImportedResource] = []
        skill_service = SkillService(session=self.session, role=self.role)
        for source_id, spec in sorted(skills.items()):
            skill = await self.session.scalar(
                select(Skill).where(
                    Skill.workspace_id == self.workspace_id,
                    Skill.name == spec.slug,
                )
            )
            if skill is None:
                skill = Skill(
                    workspace_id=self.workspace_id,
                    name=spec.slug,
                    description=getattr(spec, "description", None),
                    draft_revision=0,
                )
                self.session.add(skill)
                await self.session.flush()
            else:
                skill.description = getattr(spec, "description", None)

            file_refs: list[tuple[str, SkillFileBlobRef]] = []
            for file_spec in spec.files:
                content = spec.file_contents[file_spec.path].encode()
                blob = await skill_service._get_or_create_blob(content=content)
                file_refs.append(
                    (
                        file_spec.path,
                        SkillFileBlobRef(
                            blob=blob,
                            content_type=skill_service._guess_content_type(
                                file_spec.path
                            ),
                        ),
                    )
                )

            version_number = spec.current_version or 1
            version = await self.session.scalar(
                select(SkillVersion).where(
                    SkillVersion.workspace_id == self.workspace_id,
                    SkillVersion.skill_id == skill.id,
                    SkillVersion.version == version_number,
                )
            )
            manifest_payload = [
                {
                    "path": path,
                    "sha256": file_ref.blob.sha256,
                    "size_bytes": file_ref.blob.size_bytes,
                    "content_type": file_ref.content_type,
                }
                for path, file_ref in sorted(file_refs, key=lambda item: item[0])
            ]
            manifest_sha256 = skill_service._compute_sha256(
                orjson.dumps(manifest_payload)
            )
            attrs = {
                "manifest_sha256": manifest_sha256,
                "file_count": len(file_refs),
                "total_size_bytes": sum(
                    file_ref.blob.size_bytes for _, file_ref in file_refs
                ),
                "name": spec.slug,
                "description": getattr(spec, "description", None),
            }
            if version is None:
                version = SkillVersion(
                    workspace_id=self.workspace_id,
                    skill_id=skill.id,
                    version=version_number,
                    **attrs,
                )
                self.session.add(version)
                await self.session.flush()
            else:
                for key, value in attrs.items():
                    setattr(version, key, value)
                await self.session.execute(
                    sa.delete(SkillVersionFile).where(
                        SkillVersionFile.workspace_id == self.workspace_id,
                        SkillVersionFile.skill_version_id == version.id,
                    )
                )

            for path, file_ref in sorted(file_refs, key=lambda item: item[0]):
                self.session.add(
                    SkillVersionFile(
                        workspace_id=self.workspace_id,
                        skill_version_id=version.id,
                        path=path,
                        blob_id=file_ref.blob.id,
                        content_type=file_ref.content_type,
                    )
                )
            skill.current_version_id = version.id
            self.session.add(skill)
            await self.session.flush()
            imported.append(
                ImportedResource(
                    resource_type=SyncResourceType.SKILL,
                    source_id=source_id,
                    source_path=skill_source_path(source_id),
                    local_id=skill.id,
                )
            )
        return imported

    async def _import_agent_presets(
        self,
        presets: dict[str, AgentPresetResourceSpec],
    ) -> list[ImportedResource]:
        imported: list[ImportedResource] = []
        preset_by_slug: dict[str, AgentPreset] = {}
        for _source_id, spec in sorted(presets.items()):
            preset = await self.session.scalar(
                select(AgentPreset)
                .where(
                    AgentPreset.workspace_id == self.workspace_id,
                    AgentPreset.slug == spec.slug,
                )
                .options(selectinload(AgentPreset.tags))
            )
            if preset is None:
                preset = AgentPreset(
                    workspace_id=self.workspace_id,
                    slug=spec.slug,
                    name=spec.name,
                    model_name=getattr(spec, "model_name", None)
                    or DEFAULT_AGENT_MODEL_NAME,
                    model_provider=getattr(spec, "model_provider", None)
                    or DEFAULT_AGENT_MODEL_PROVIDER,
                    agents=AgentSubagentsConfig().model_dump(mode="json"),
                )
            preset.name = spec.name
            preset.instructions = spec.instructions
            preset.actions = spec.actions or None
            preset.tool_approvals = _tool_approvals(spec.tool_approvals)
            preset.model_name = getattr(spec, "model_name", None) or preset.model_name
            preset.model_provider = (
                getattr(spec, "model_provider", None) or preset.model_provider
            )
            folder = await self._ensure_agent_folder(spec.folder_path)
            preset.folder_id = folder.id if folder is not None else None
            self.session.add(preset)
            await self.session.flush()
            await self._replace_agent_tags(preset, spec.tags)
            preset_by_slug[spec.slug] = preset

        for source_id, spec in sorted(presets.items()):
            preset = preset_by_slug[spec.slug]
            preset.agents = await self._resolved_subagents_config(spec)
            self.session.add(preset)
            await self.session.flush()
            version = await self._upsert_agent_preset_version(preset)
            await self._replace_preset_skill_bindings(preset, version, spec)
            preset.current_version_id = version.id
            self.session.add(preset)
            await self.session.flush()
            imported.append(
                ImportedResource(
                    resource_type=SyncResourceType.AGENT_PRESET,
                    source_id=source_id,
                    source_path=agent_preset_source_path(source_id),
                    local_id=preset.id,
                )
            )
        return imported

    async def _ensure_agent_folder(self, folder_path: str | None) -> AgentFolder | None:
        if not folder_path:
            return None
        segments = [segment for segment in folder_path.strip("/").split("/") if segment]
        if not segments:
            return None
        current_path = "/"
        folder: AgentFolder | None = None
        for segment in segments:
            current_path = f"{current_path}{segment}/"
            folder = await self.session.scalar(
                select(AgentFolder).where(
                    AgentFolder.workspace_id == self.workspace_id,
                    AgentFolder.path == current_path,
                )
            )
            if folder is None:
                folder = AgentFolder(
                    workspace_id=self.workspace_id,
                    name=segment,
                    path=current_path,
                )
                self.session.add(folder)
                await self.session.flush()
        return folder

    async def _replace_agent_tags(
        self,
        preset: AgentPreset,
        tag_names: list[str],
    ) -> None:
        await self.session.execute(
            sa.delete(AgentTagLink).where(AgentTagLink.preset_id == preset.id)
        )
        tag_ids: list[uuid.UUID] = []
        for name in sorted(dict.fromkeys(tag_names)):
            ref = slugify(name, separator="-") or name
            tag = await self.session.scalar(
                select(AgentTag).where(
                    AgentTag.workspace_id == self.workspace_id,
                    AgentTag.ref == ref,
                )
            )
            if tag is None:
                tag = AgentTag(
                    workspace_id=self.workspace_id,
                    name=name,
                    ref=ref,
                )
            else:
                tag.name = name
            self.session.add(tag)
            await self.session.flush()
            tag_ids.append(tag.id)
        for tag_id in tag_ids:
            self.session.add(AgentTagLink(tag_id=tag_id, preset_id=preset.id))
        await self.session.flush()

    async def _resolved_subagents_config(
        self,
        spec: AgentPresetResourceSpec,
    ) -> dict[str, Any]:
        if not spec.subagents:
            return AgentSubagentsConfig().model_dump(mode="json")

        subagents: list[dict[str, Any]] = []
        for subagent in spec.subagents:
            child = await self.session.scalar(
                select(AgentPreset).where(
                    AgentPreset.workspace_id == self.workspace_id,
                    AgentPreset.slug == subagent.slug,
                )
            )
            if child is None or child.current_version_id is None:
                continue
            subagents.append(
                {
                    "preset": child.slug,
                    "preset_id": str(child.id),
                    "preset_version_id": str(child.current_version_id),
                    "preset_version": None,
                    "name": None,
                    "description": None,
                    "max_turns": None,
                }
            )
        return {"enabled": bool(subagents), "subagents": subagents}

    async def _upsert_agent_preset_version(
        self,
        preset: AgentPreset,
    ) -> AgentPresetVersion:
        version = None
        if preset.current_version_id is not None:
            version = await self.session.scalar(
                select(AgentPresetVersion).where(
                    AgentPresetVersion.workspace_id == self.workspace_id,
                    AgentPresetVersion.preset_id == preset.id,
                    AgentPresetVersion.id == preset.current_version_id,
                )
            )
        if version is None:
            version = AgentPresetVersion(
                workspace_id=self.workspace_id,
                preset_id=preset.id,
                version=1,
            )
        attrs = {
            "instructions": preset.instructions,
            "model_name": preset.model_name,
            "model_provider": preset.model_provider,
            "catalog_id": preset.catalog_id,
            "base_url": preset.base_url,
            "output_type": preset.output_type,
            "actions": preset.actions,
            "namespaces": preset.namespaces,
            "tool_approvals": preset.tool_approvals,
            "mcp_integrations": preset.mcp_integrations,
            "agents": preset.agents,
            "retries": preset.retries,
            "enable_thinking": preset.enable_thinking,
            "enable_internet_access": preset.enable_internet_access,
        }
        for key, value in attrs.items():
            setattr(version, key, value)
        self.session.add(version)
        await self.session.flush()
        return version

    async def _replace_preset_skill_bindings(
        self,
        preset: AgentPreset,
        version: AgentPresetVersion,
        spec: AgentPresetResourceSpec,
    ) -> None:
        await self.session.execute(
            sa.delete(AgentPresetSkill).where(
                AgentPresetSkill.workspace_id == self.workspace_id,
                AgentPresetSkill.preset_id == preset.id,
            )
        )
        await self.session.execute(
            sa.delete(AgentPresetVersionSkill).where(
                AgentPresetVersionSkill.workspace_id == self.workspace_id,
                AgentPresetVersionSkill.preset_version_id == version.id,
            )
        )
        for binding in spec.skills:
            skill, skill_version = await self._skill_binding_targets(binding)
            if skill is None or skill_version is None:
                continue
            self.session.add(
                AgentPresetSkill(
                    workspace_id=self.workspace_id,
                    preset_id=preset.id,
                    skill_id=skill.id,
                    skill_version_id=skill_version.id,
                )
            )
            self.session.add(
                AgentPresetVersionSkill(
                    workspace_id=self.workspace_id,
                    preset_version_id=version.id,
                    skill_id=skill.id,
                    skill_version_id=skill_version.id,
                )
            )
        await self.session.flush()

    async def _skill_binding_targets(
        self,
        binding: AgentPresetSkillBinding,
    ) -> tuple[Skill | None, SkillVersion | None]:
        skill = await self.session.scalar(
            select(Skill).where(
                Skill.workspace_id == self.workspace_id,
                Skill.name == binding.slug,
            )
        )
        if skill is None:
            return None, None
        version_number = binding.version
        stmt = select(SkillVersion).where(
            SkillVersion.workspace_id == self.workspace_id,
            SkillVersion.skill_id == skill.id,
        )
        if version_number is not None:
            stmt = stmt.where(SkillVersion.version == version_number)
        else:
            stmt = stmt.where(SkillVersion.id == skill.current_version_id)
        version = await self.session.scalar(stmt)
        return skill, version


def _sql_type(value: Any) -> SqlType:
    raw = str(value).replace("-", "_").upper()
    return SqlType(raw)


def _case_field_kind(value: Any) -> CaseFieldKind | None:
    if value is None:
        return None
    try:
        return CaseFieldKind(str(value))
    except ValueError:
        return None


def _duration_anchor(
    spec: CaseDurationResourceSpec,
    key: str,
) -> dict[str, Any]:
    data = getattr(spec, key, None)
    if not isinstance(data, dict):
        data = {}
    return {
        "event_type": CaseEventType(data.get("event", "case_created")),
        "selection": CaseDurationAnchorSelection(data.get("selection", "first")),
        "timestamp_path": data.get("timestamp_path", "created_at"),
        "field_filters": data.get("field_filters", {}),
    }


def _tool_approvals(value: dict[str, Any]) -> dict[str, bool] | None:
    if not value:
        return None
    return {
        key: bool(raw_value == "manual" or raw_value is True)
        for key, raw_value in value.items()
    }
