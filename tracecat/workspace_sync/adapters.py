"""Workspace sync resource adapter registry."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, cast

from pydantic import BaseModel

from tracecat.workspace_sync.enums import SyncResourceType
from tracecat.workspace_sync.schemas import (
    AGENT_PRESET_ROOT,
    CASE_DROPDOWN_ROOT,
    CASE_DURATION_ROOT,
    CASE_FIELD_ROOT,
    CASE_TAG_ROOT,
    SECRET_METADATA_ROOT,
    SKILL_ROOT,
    TABLE_ROOT,
    VARIABLE_ROOT,
    AgentPresetResourceSpec,
    CaseDropdownResourceSpec,
    CaseDurationResourceSpec,
    CaseFieldResourceSpec,
    CaseTagResourceSpec,
    SecretMetadataResourceSpec,
    SkillResourceSpec,
    TableResourceSpec,
    VariableResourceSpec,
    WorkflowResourceSpec,
    WorkspaceManifestResources,
    WorkspaceSpec,
)
from tracecat.workspace_sync.workflow import (
    workflow_source_id_from_path,
    workflow_source_path,
)

AGENT_PRESET_FILENAME = "preset.yml"
SKILL_FILENAME = "skill.yml"
SKILL_FILES_DIR = "files"
TABLE_FILENAME = "table.yml"

type WorkspaceSpecField = Literal[
    "workflows",
    "agent_presets",
    "skills",
    "tables",
    "case_tags",
    "case_fields",
    "case_dropdowns",
    "case_durations",
    "variables",
    "secret_metadata",
]

SourceIdMatcher = Callable[[str, WorkspaceManifestResources], str | None]
ExtraPathMatcher = Callable[[str, WorkspaceManifestResources], tuple[str, str] | None]
ExtraFileSerializer = Callable[[str, BaseModel], dict[str, str]]


def _no_extra_files(_source_id: str, _spec: BaseModel) -> dict[str, str]:
    return {}


@dataclass(frozen=True, slots=True)
class WorkspaceSyncResourceAdapter:
    """Adapter metadata for one Git-backed workspace resource type."""

    resource_type: SyncResourceType
    spec_attr: WorkspaceSpecField
    model: type[BaseModel]
    source_path: Callable[[str], str]
    source_id_from_path: SourceIdMatcher
    project_method: str | None = None
    import_method: str | None = None
    extra_path_from_path: ExtraPathMatcher | None = None
    serialize_extra_files: ExtraFileSerializer = _no_extra_files

    def specs(self, spec: WorkspaceSpec) -> dict[str, BaseModel]:
        return cast(dict[str, BaseModel], getattr(spec, self.spec_attr))


def agent_preset_source_path(source_id: str) -> str:
    return f"{AGENT_PRESET_ROOT}/{source_id}/{AGENT_PRESET_FILENAME}"


def skill_source_path(source_id: str) -> str:
    return f"{SKILL_ROOT}/{source_id}/{SKILL_FILENAME}"


def skill_file_source_path(source_id: str, file_path: str) -> str:
    return f"{SKILL_ROOT}/{source_id}/{SKILL_FILES_DIR}/{file_path}"


def table_source_path(source_id: str) -> str:
    return f"{TABLE_ROOT}/{source_id}/{TABLE_FILENAME}"


def table_rows_source_path(source_id: str, rows_path: str) -> str:
    return f"{TABLE_ROOT}/{source_id}/{rows_path}"


def case_tag_source_path(source_id: str) -> str:
    return f"{CASE_TAG_ROOT}/{source_id}.yml"


def case_field_source_path(source_id: str) -> str:
    return f"{CASE_FIELD_ROOT}/{source_id}.yml"


def case_dropdown_source_path(source_id: str) -> str:
    return f"{CASE_DROPDOWN_ROOT}/{source_id}.yml"


def case_duration_source_path(source_id: str) -> str:
    return f"{CASE_DURATION_ROOT}/{source_id}.yml"


def variable_source_path(source_id: str) -> str:
    return f"{VARIABLE_ROOT}/{source_id}.yml"


def secret_metadata_source_path(source_id: str) -> str:
    return f"{SECRET_METADATA_ROOT}/{source_id}.yml"


def workspace_spec_from_maps(
    specs_by_attr: Mapping[str, Mapping[str, Any]],
) -> WorkspaceSpec:
    return WorkspaceSpec.model_validate(
        {
            adapter.spec_attr: dict(
                sorted(specs_by_attr.get(adapter.spec_attr, {}).items())
            )
            for adapter in WORKSPACE_RESOURCE_ADAPTERS
        }
    )


def _workflow_source_id(path: str, roots: WorkspaceManifestResources) -> str | None:
    return workflow_source_id_from_path(
        path,
        workflow_root=roots.workflows.strip("/"),
    )


def _compound_yaml_source_id(
    path: str,
    *,
    root: str,
    filename: str,
) -> str | None:
    parts = _path_parts(path)
    root_parts = _path_parts(root)
    if len(parts) != len(root_parts) + 2:
        return None
    if parts[: len(root_parts)] != root_parts or parts[-1] != filename:
        return None
    source_id = parts[-2]
    return source_id or None


def _compound_extra_path(path: str, *, root: str) -> tuple[str, str] | None:
    parts = _path_parts(path)
    root_parts = _path_parts(root)
    if len(parts) < len(root_parts) + 2:
        return None
    if parts[: len(root_parts)] != root_parts:
        return None
    source_id = parts[len(root_parts)]
    relpath = "/".join(parts[len(root_parts) + 1 :])
    if not source_id or not relpath:
        return None
    return source_id, relpath


def _single_yaml_source_id(path: str, *, root: str) -> str | None:
    parts = _path_parts(path)
    root_parts = _path_parts(root)
    if len(parts) != len(root_parts) + 1:
        return None
    if parts[: len(root_parts)] != root_parts:
        return None
    filename = parts[-1]
    if not filename.endswith(".yml"):
        return None
    return filename.removesuffix(".yml") or None


def _environment_yaml_source_id(path: str, *, root: str) -> str | None:
    parts = _path_parts(path)
    root_parts = _path_parts(root)
    if len(parts) != len(root_parts) + 2:
        return None
    if parts[: len(root_parts)] != root_parts:
        return None
    environment = parts[-2]
    filename = parts[-1]
    if not environment or not filename.endswith(".yml"):
        return None
    name = filename.removesuffix(".yml")
    if not name:
        return None
    return f"{environment}/{name}"


def _skill_file_path(
    path: str,
    roots: WorkspaceManifestResources,
) -> tuple[str, str] | None:
    parts = _path_parts(path)
    root_parts = _path_parts(roots.skills)
    if len(parts) < len(root_parts) + 3:
        return None
    if parts[: len(root_parts)] != root_parts:
        return None
    source_id = parts[len(root_parts)]
    files_dir = parts[len(root_parts) + 1]
    if not source_id or files_dir != SKILL_FILES_DIR:
        return None
    file_path = "/".join(parts[len(root_parts) + 2 :])
    return (source_id, file_path) if file_path else None


def _path_parts(path: str) -> list[str]:
    return [part for part in path.strip("/").split("/") if part]


def _compound_yaml_matcher(
    root_attr: str,
    filename: str,
) -> SourceIdMatcher:
    return lambda path, roots: _compound_yaml_source_id(
        path,
        root=str(getattr(roots, root_attr)),
        filename=filename,
    )


def _single_yaml_matcher(root_attr: str) -> SourceIdMatcher:
    return lambda path, roots: _single_yaml_source_id(
        path,
        root=str(getattr(roots, root_attr)),
    )


def _environment_yaml_matcher(root_attr: str) -> SourceIdMatcher:
    return lambda path, roots: _environment_yaml_source_id(
        path,
        root=str(getattr(roots, root_attr)),
    )


def _skill_extra_files(source_id: str, spec: BaseModel) -> dict[str, str]:
    skill = cast(SkillResourceSpec, spec)
    return {
        skill_file_source_path(source_id, file_path): content
        for file_path, content in sorted(skill.file_contents.items())
    }


def _table_extra_files(source_id: str, spec: BaseModel) -> dict[str, str]:
    table = cast(TableResourceSpec, spec)
    if not table.rows or not table.rows_path:
        return {}
    return {
        table_rows_source_path(source_id, table.rows_path): "".join(
            json.dumps(row, sort_keys=True) + "\n" for row in table.rows
        )
    }


WORKFLOW_RESOURCE_ADAPTER = WorkspaceSyncResourceAdapter(
    resource_type=SyncResourceType.WORKFLOW,
    spec_attr="workflows",
    model=WorkflowResourceSpec,
    source_path=workflow_source_path,
    source_id_from_path=_workflow_source_id,
)
AGENT_PRESET_RESOURCE_ADAPTER = WorkspaceSyncResourceAdapter(
    resource_type=SyncResourceType.AGENT_PRESET,
    spec_attr="agent_presets",
    model=AgentPresetResourceSpec,
    source_path=agent_preset_source_path,
    source_id_from_path=_compound_yaml_matcher("agent_presets", AGENT_PRESET_FILENAME),
    project_method="_project_agent_presets",
    import_method="_import_agent_presets",
)
SKILL_RESOURCE_ADAPTER = WorkspaceSyncResourceAdapter(
    resource_type=SyncResourceType.SKILL,
    spec_attr="skills",
    model=SkillResourceSpec,
    source_path=skill_source_path,
    source_id_from_path=_compound_yaml_matcher("skills", SKILL_FILENAME),
    project_method="_project_skills",
    import_method="_import_skills",
    extra_path_from_path=_skill_file_path,
    serialize_extra_files=_skill_extra_files,
)
TABLE_RESOURCE_ADAPTER = WorkspaceSyncResourceAdapter(
    resource_type=SyncResourceType.TABLE,
    spec_attr="tables",
    model=TableResourceSpec,
    source_path=table_source_path,
    source_id_from_path=_compound_yaml_matcher("tables", TABLE_FILENAME),
    project_method="_project_tables",
    import_method="_import_tables",
    extra_path_from_path=lambda path, roots: _compound_extra_path(
        path,
        root=roots.tables,
    ),
    serialize_extra_files=_table_extra_files,
)
CASE_TAG_RESOURCE_ADAPTER = WorkspaceSyncResourceAdapter(
    resource_type=SyncResourceType.CASE_TAG,
    spec_attr="case_tags",
    model=CaseTagResourceSpec,
    source_path=case_tag_source_path,
    source_id_from_path=_single_yaml_matcher("case_tags"),
    project_method="_project_case_tags",
    import_method="_import_case_tags",
)
CASE_FIELD_RESOURCE_ADAPTER = WorkspaceSyncResourceAdapter(
    resource_type=SyncResourceType.CASE_FIELD,
    spec_attr="case_fields",
    model=CaseFieldResourceSpec,
    source_path=case_field_source_path,
    source_id_from_path=_single_yaml_matcher("case_fields"),
    project_method="_project_case_fields",
    import_method="_import_case_fields",
)
CASE_DROPDOWN_RESOURCE_ADAPTER = WorkspaceSyncResourceAdapter(
    resource_type=SyncResourceType.CASE_DROPDOWN,
    spec_attr="case_dropdowns",
    model=CaseDropdownResourceSpec,
    source_path=case_dropdown_source_path,
    source_id_from_path=_single_yaml_matcher("case_dropdowns"),
    project_method="_project_case_dropdowns",
    import_method="_import_case_dropdowns",
)
CASE_DURATION_RESOURCE_ADAPTER = WorkspaceSyncResourceAdapter(
    resource_type=SyncResourceType.CASE_DURATION,
    spec_attr="case_durations",
    model=CaseDurationResourceSpec,
    source_path=case_duration_source_path,
    source_id_from_path=_single_yaml_matcher("case_durations"),
    project_method="_project_case_durations",
    import_method="_import_case_durations",
)
VARIABLE_RESOURCE_ADAPTER = WorkspaceSyncResourceAdapter(
    resource_type=SyncResourceType.VARIABLE,
    spec_attr="variables",
    model=VariableResourceSpec,
    source_path=variable_source_path,
    source_id_from_path=_environment_yaml_matcher("variables"),
    project_method="_project_variables",
    import_method="_import_variables",
)
SECRET_METADATA_RESOURCE_ADAPTER = WorkspaceSyncResourceAdapter(
    resource_type=SyncResourceType.SECRET_METADATA,
    spec_attr="secret_metadata",
    model=SecretMetadataResourceSpec,
    source_path=secret_metadata_source_path,
    source_id_from_path=_environment_yaml_matcher("secret_metadata"),
    project_method="_project_secret_metadata",
    import_method="_import_secret_metadata",
)

NON_WORKFLOW_RESOURCE_ADAPTERS = (
    AGENT_PRESET_RESOURCE_ADAPTER,
    SKILL_RESOURCE_ADAPTER,
    TABLE_RESOURCE_ADAPTER,
    CASE_TAG_RESOURCE_ADAPTER,
    CASE_FIELD_RESOURCE_ADAPTER,
    CASE_DROPDOWN_RESOURCE_ADAPTER,
    CASE_DURATION_RESOURCE_ADAPTER,
    VARIABLE_RESOURCE_ADAPTER,
    SECRET_METADATA_RESOURCE_ADAPTER,
)
NON_WORKFLOW_IMPORT_ADAPTERS = (
    VARIABLE_RESOURCE_ADAPTER,
    SECRET_METADATA_RESOURCE_ADAPTER,
    CASE_TAG_RESOURCE_ADAPTER,
    CASE_DROPDOWN_RESOURCE_ADAPTER,
    CASE_DURATION_RESOURCE_ADAPTER,
    CASE_FIELD_RESOURCE_ADAPTER,
    TABLE_RESOURCE_ADAPTER,
    SKILL_RESOURCE_ADAPTER,
    AGENT_PRESET_RESOURCE_ADAPTER,
)
WORKSPACE_RESOURCE_ADAPTERS = (
    WORKFLOW_RESOURCE_ADAPTER,
    *NON_WORKFLOW_RESOURCE_ADAPTERS,
)
RESOURCE_ADAPTERS_BY_TYPE = {
    adapter.resource_type: adapter for adapter in WORKSPACE_RESOURCE_ADAPTERS
}
