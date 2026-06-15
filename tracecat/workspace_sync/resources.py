"""Generic resource parsing and validation for workspace sync."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Callable
from typing import Any, TypeVar

import yaml
from pydantic import BaseModel, ValidationError

from tracecat.sync import PullDiagnostic
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
    WorkspaceManifest,
    WorkspaceSpec,
)
from tracecat.workspace_sync.workflow import (
    is_workflow_definition_path,
    parse_workflow_spec,
    serialize_workflow_spec,
    workflow_source_path,
)

ModelT = TypeVar("ModelT", bound=BaseModel)

AGENT_PRESET_FILENAME = "preset.yml"
SKILL_FILENAME = "skill.yml"
SKILL_FILES_DIR = "files"
TABLE_FILENAME = "table.yml"


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


def parse_workspace_spec_files(
    files: dict[str, str],
    *,
    manifest: WorkspaceManifest,
) -> tuple[WorkspaceSpec, list[PullDiagnostic]]:
    """Parse manifest-declared resource files into a workspace spec."""
    diagnostics: list[PullDiagnostic] = []
    roots = manifest.resources

    workflows = {}
    agent_presets: dict[str, AgentPresetResourceSpec] = {}
    skills: dict[str, SkillResourceSpec] = {}
    tables: dict[str, TableResourceSpec] = {}
    case_tags: dict[str, CaseTagResourceSpec] = {}
    case_fields: dict[str, CaseFieldResourceSpec] = {}
    case_dropdowns: dict[str, CaseDropdownResourceSpec] = {}
    case_durations: dict[str, CaseDurationResourceSpec] = {}
    variables: dict[str, VariableResourceSpec] = {}
    secret_metadata: dict[str, SecretMetadataResourceSpec] = {}
    skill_file_contents: dict[str, dict[str, str]] = defaultdict(dict)
    table_row_files: dict[tuple[str, str], str] = {}

    workflow_root = roots.workflows.strip("/")
    for path, content in sorted(files.items()):
        if is_workflow_definition_path(path, workflow_root=workflow_root):
            workflow, diagnostic = parse_workflow_spec(
                path,
                content,
                workflow_root=workflow_root,
            )
            if diagnostic is not None:
                diagnostics.append(diagnostic)
            elif workflow is not None:
                workflows[workflow.id] = workflow
            continue

        if source_id := _compound_yaml_source_id(
            path,
            root=roots.agent_presets,
            filename=AGENT_PRESET_FILENAME,
        ):
            _parse_yaml_resource(
                path,
                content,
                expected_source_id=source_id,
                model=AgentPresetResourceSpec,
                destination=agent_presets,
                diagnostics=diagnostics,
            )
            continue

        if source_id := _compound_yaml_source_id(
            path,
            root=roots.skills,
            filename=SKILL_FILENAME,
        ):
            _parse_yaml_resource(
                path,
                content,
                expected_source_id=source_id,
                model=SkillResourceSpec,
                destination=skills,
                diagnostics=diagnostics,
            )
            continue

        if skill_file := _skill_file_path(
            path,
            root=roots.skills,
        ):
            source_id, file_path = skill_file
            skill_file_contents[source_id][file_path] = content
            continue

        if source_id := _compound_yaml_source_id(
            path,
            root=roots.tables,
            filename=TABLE_FILENAME,
        ):
            _parse_yaml_resource(
                path,
                content,
                expected_source_id=source_id,
                model=TableResourceSpec,
                destination=tables,
                diagnostics=diagnostics,
            )
            continue

        if table_rows := _compound_extra_path(path, root=roots.tables):
            source_id, rows_path = table_rows
            table_row_files[(source_id, rows_path)] = content
            continue

        if source_id := _single_yaml_source_id(path, root=roots.case_tags):
            _parse_yaml_resource(
                path,
                content,
                expected_source_id=source_id,
                model=CaseTagResourceSpec,
                destination=case_tags,
                diagnostics=diagnostics,
            )
            continue

        if source_id := _single_yaml_source_id(path, root=roots.case_fields):
            _parse_yaml_resource(
                path,
                content,
                expected_source_id=source_id,
                model=CaseFieldResourceSpec,
                destination=case_fields,
                diagnostics=diagnostics,
            )
            continue

        if source_id := _single_yaml_source_id(path, root=roots.case_dropdowns):
            _parse_yaml_resource(
                path,
                content,
                expected_source_id=source_id,
                model=CaseDropdownResourceSpec,
                destination=case_dropdowns,
                diagnostics=diagnostics,
            )
            continue

        if source_id := _single_yaml_source_id(path, root=roots.case_durations):
            _parse_yaml_resource(
                path,
                content,
                expected_source_id=source_id,
                model=CaseDurationResourceSpec,
                destination=case_durations,
                diagnostics=diagnostics,
            )
            continue

        if source_id := _environment_yaml_source_id(path, root=roots.variables):
            _parse_yaml_resource(
                path,
                content,
                expected_source_id=source_id,
                model=VariableResourceSpec,
                destination=variables,
                diagnostics=diagnostics,
            )
            continue

        if source_id := _environment_yaml_source_id(path, root=roots.secret_metadata):
            _parse_yaml_resource(
                path,
                content,
                expected_source_id=source_id,
                model=SecretMetadataResourceSpec,
                destination=secret_metadata,
                diagnostics=diagnostics,
            )
            continue

    skills = _attach_skill_files(
        skills,
        skill_file_contents=skill_file_contents,
        diagnostics=diagnostics,
    )
    tables = _attach_table_rows(
        tables,
        table_row_files=table_row_files,
        diagnostics=diagnostics,
    )
    spec = WorkspaceSpec(
        workflows=dict(sorted(workflows.items())),
        agent_presets=dict(sorted(agent_presets.items())),
        skills=dict(sorted(skills.items())),
        tables=dict(sorted(tables.items())),
        case_tags=dict(sorted(case_tags.items())),
        case_fields=dict(sorted(case_fields.items())),
        case_dropdowns=dict(sorted(case_dropdowns.items())),
        case_durations=dict(sorted(case_durations.items())),
        variables=dict(sorted(variables.items())),
        secret_metadata=dict(sorted(secret_metadata.items())),
    )
    diagnostics.extend(validate_workspace_dependencies(spec))
    return spec, diagnostics


def serialize_workspace_spec_files(
    *,
    manifest: WorkspaceManifest,
    spec: WorkspaceSpec,
    manifest_filename: str,
    manifest_serializer: Callable[[WorkspaceManifest], str],
) -> dict[str, str]:
    files = {manifest_filename: manifest_serializer(manifest)}
    for source_id, workflow_spec in sorted(spec.workflows.items()):
        files[workflow_source_path(source_id)] = serialize_workflow_spec(workflow_spec)
    for source_id, preset_spec in sorted(spec.agent_presets.items()):
        files[agent_preset_source_path(source_id)] = _serialize_yaml_model(preset_spec)
    for source_id, skill_spec in sorted(spec.skills.items()):
        files[skill_source_path(source_id)] = _serialize_yaml_model(skill_spec)
        for file_path, content in sorted(skill_spec.file_contents.items()):
            files[skill_file_source_path(source_id, file_path)] = content
    for source_id, table_spec in sorted(spec.tables.items()):
        files[table_source_path(source_id)] = _serialize_yaml_model(table_spec)
        if table_spec.rows and table_spec.rows_path:
            files[table_rows_source_path(source_id, table_spec.rows_path)] = "".join(
                json.dumps(row, sort_keys=True) + "\n" for row in table_spec.rows
            )
    for source_id, tag_spec in sorted(spec.case_tags.items()):
        files[case_tag_source_path(source_id)] = _serialize_yaml_model(tag_spec)
    for source_id, field_spec in sorted(spec.case_fields.items()):
        files[case_field_source_path(source_id)] = _serialize_yaml_model(field_spec)
    for source_id, dropdown_spec in sorted(spec.case_dropdowns.items()):
        files[case_dropdown_source_path(source_id)] = _serialize_yaml_model(
            dropdown_spec
        )
    for source_id, duration_spec in sorted(spec.case_durations.items()):
        files[case_duration_source_path(source_id)] = _serialize_yaml_model(
            duration_spec
        )
    for source_id, variable_spec in sorted(spec.variables.items()):
        files[variable_source_path(source_id)] = _serialize_yaml_model(variable_spec)
    for source_id, secret_spec in sorted(spec.secret_metadata.items()):
        files[secret_metadata_source_path(source_id)] = _serialize_yaml_model(
            secret_spec
        )
    return dict(sorted(files.items()))


def validate_workspace_dependencies(spec: WorkspaceSpec) -> list[PullDiagnostic]:
    diagnostics: list[PullDiagnostic] = []
    workflow_aliases = {
        workflow.alias for workflow in spec.workflows.values() if workflow.alias
    }
    preset_slugs = {preset.slug for preset in spec.agent_presets.values()}
    skill_slugs = {skill.slug for skill in spec.skills.values()}

    for source_id, workflow in sorted(spec.workflows.items()):
        for alias in sorted(_workflow_execute_aliases(workflow.definition)):
            if alias not in workflow_aliases:
                diagnostics.append(
                    PullDiagnostic(
                        workflow_path=workflow_source_path(source_id),
                        workflow_title=workflow.definition.title,
                        error_type="dependency",
                        message=f"Workflow references missing child workflow alias {alias!r}",
                        details={"workflow_source_id": source_id, "alias": alias},
                    )
                )
        for preset_slug in sorted(_workflow_preset_slugs(workflow.definition)):
            if preset_slug not in preset_slugs:
                diagnostics.append(
                    PullDiagnostic(
                        workflow_path=workflow_source_path(source_id),
                        workflow_title=workflow.definition.title,
                        error_type="dependency",
                        message=f"Workflow references missing agent preset slug {preset_slug!r}",
                        details={
                            "workflow_source_id": source_id,
                            "preset_slug": preset_slug,
                        },
                    )
                )

    preset_graph: dict[str, list[str]] = {}
    for source_id, preset in sorted(spec.agent_presets.items()):
        preset_graph[preset.slug] = [subagent.slug for subagent in preset.subagents]
        for skill in preset.skills:
            if skill.slug not in skill_slugs:
                diagnostics.append(
                    PullDiagnostic(
                        workflow_path=agent_preset_source_path(source_id),
                        workflow_title=preset.name,
                        error_type="dependency",
                        message=f"Agent preset references missing skill slug {skill.slug!r}",
                        details={"preset_slug": preset.slug, "skill_slug": skill.slug},
                    )
                )
        for subagent in preset.subagents:
            if subagent.slug not in preset_slugs:
                diagnostics.append(
                    PullDiagnostic(
                        workflow_path=agent_preset_source_path(source_id),
                        workflow_title=preset.name,
                        error_type="dependency",
                        message=f"Agent preset references missing subagent slug {subagent.slug!r}",
                        details={
                            "preset_slug": preset.slug,
                            "subagent_slug": subagent.slug,
                        },
                    )
                )

    if cycle := _find_cycle(preset_graph):
        diagnostics.append(
            PullDiagnostic(
                workflow_path="",
                workflow_title=None,
                error_type="dependency",
                message="Cyclic agent preset subagent reference detected: "
                + " -> ".join(cycle),
                details={"cycle": cycle},
            )
        )
    return diagnostics


def workflow_execute_aliases(definition: BaseModel) -> set[str]:
    return _workflow_execute_aliases(definition)


def workflow_preset_slugs(definition: BaseModel) -> set[str]:
    return _workflow_preset_slugs(definition)


def _parse_yaml_resource(
    path: str,
    content: str,
    *,
    expected_source_id: str,
    model: type[ModelT],
    destination: dict[str, ModelT],
    diagnostics: list[PullDiagnostic],
) -> None:
    yaml_data: dict[str, Any] | None = None
    try:
        raw = yaml.safe_load(content)
        if not isinstance(raw, dict) or not raw:
            diagnostics.append(
                PullDiagnostic(
                    workflow_path=path,
                    workflow_title=None,
                    error_type="parse",
                    message="Empty or invalid resource YAML file",
                    details={},
                )
            )
            return
        yaml_data = raw
        if "id" not in raw:
            raw = {**raw, "id": expected_source_id}
        spec = model.model_validate(raw)
        spec_id = getattr(spec, "id", None)
        if spec_id != expected_source_id:
            diagnostics.append(
                PullDiagnostic(
                    workflow_path=path,
                    workflow_title=_resource_title(yaml_data),
                    error_type="validation",
                    message="Resource source id does not match its repository path",
                    details={
                        "path_source_id": expected_source_id,
                        "spec_id": spec_id,
                    },
                )
            )
            return
        destination[expected_source_id] = spec
    except yaml.YAMLError as e:
        diagnostics.append(
            PullDiagnostic(
                workflow_path=path,
                workflow_title=None,
                error_type="parse",
                message=f"YAML parsing error: {str(e)}",
                details={"yaml_error": str(e)},
            )
        )
    except ValidationError as e:
        diagnostics.append(
            PullDiagnostic(
                workflow_path=path,
                workflow_title=_resource_title(yaml_data),
                error_type="validation",
                message=f"Validation error: {str(e)}",
                details={"validation_errors": e.errors()},
            )
        )
    except Exception as e:
        diagnostics.append(
            PullDiagnostic(
                workflow_path=path,
                workflow_title=_resource_title(yaml_data),
                error_type="parse",
                message=f"Unexpected parsing error: {str(e)}",
                details={"error": str(e)},
            )
        )


def _attach_skill_files(
    skills: dict[str, SkillResourceSpec],
    *,
    skill_file_contents: dict[str, dict[str, str]],
    diagnostics: list[PullDiagnostic],
) -> dict[str, SkillResourceSpec]:
    updated: dict[str, SkillResourceSpec] = {}
    for source_id, spec in skills.items():
        contents = skill_file_contents.get(source_id, {})
        for file_spec in spec.files:
            content = contents.get(file_spec.path)
            if content is None:
                diagnostics.append(
                    PullDiagnostic(
                        workflow_path=skill_source_path(source_id),
                        workflow_title=spec.name,
                        error_type="dependency",
                        message=f"Skill file {file_spec.path!r} is missing",
                        details={"skill_slug": spec.slug, "file_path": file_spec.path},
                    )
                )
                continue
            actual_hash = hashlib.sha256(content.encode()).hexdigest()
            if actual_hash != file_spec.sha256:
                diagnostics.append(
                    PullDiagnostic(
                        workflow_path=skill_file_source_path(
                            source_id,
                            file_spec.path,
                        ),
                        workflow_title=spec.name,
                        error_type="validation",
                        message=f"Skill file {file_spec.path!r} SHA256 does not match",
                        details={
                            "skill_slug": spec.slug,
                            "file_path": file_spec.path,
                            "expected_sha256": file_spec.sha256,
                            "actual_sha256": actual_hash,
                        },
                    )
                )
        updated[source_id] = spec.model_copy(update={"file_contents": contents})
    return updated


def _attach_table_rows(
    tables: dict[str, TableResourceSpec],
    *,
    table_row_files: dict[tuple[str, str], str],
    diagnostics: list[PullDiagnostic],
) -> dict[str, TableResourceSpec]:
    updated: dict[str, TableResourceSpec] = {}
    for source_id, spec in tables.items():
        rows: list[dict[str, Any]] = []
        if spec.rows_path and (
            content := table_row_files.get((source_id, spec.rows_path))
        ):
            for line_number, line in enumerate(content.splitlines(), start=1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as e:
                    diagnostics.append(
                        PullDiagnostic(
                            workflow_path=table_rows_source_path(
                                source_id,
                                spec.rows_path,
                            ),
                            workflow_title=spec.name,
                            error_type="parse",
                            message=f"Invalid table JSONL row at line {line_number}: {e}",
                            details={
                                "table": spec.name,
                                "line_number": line_number,
                                "error": str(e),
                            },
                        )
                    )
                    continue
                if not isinstance(row, dict):
                    diagnostics.append(
                        PullDiagnostic(
                            workflow_path=table_rows_source_path(
                                source_id,
                                spec.rows_path,
                            ),
                            workflow_title=spec.name,
                            error_type="validation",
                            message=f"Table row at line {line_number} is not an object",
                            details={
                                "table": spec.name,
                                "line_number": line_number,
                            },
                        )
                    )
                    continue
                rows.append(row)
        updated[source_id] = spec.model_copy(update={"rows": rows})
    return updated


def _compound_yaml_source_id(path: str, *, root: str, filename: str) -> str | None:
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


def _skill_file_path(path: str, *, root: str) -> tuple[str, str] | None:
    parts = _path_parts(path)
    root_parts = _path_parts(root)
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


def _resource_title(data: dict[str, Any] | None) -> str | None:
    if not data:
        return None
    name = data.get("name")
    if isinstance(name, str):
        return name
    title = data.get("title")
    return title if isinstance(title, str) else None


def _serialize_yaml_model(model: BaseModel) -> str:
    return yaml.safe_dump(
        model.model_dump(mode="json", exclude_none=True),
        sort_keys=False,
        allow_unicode=True,
    )


def _workflow_execute_aliases(definition: BaseModel) -> set[str]:
    aliases: set[str] = set()
    for action in _definition_actions(definition):
        if action.get("action") != "core.workflow.execute":
            continue
        args = action.get("args")
        if isinstance(args, dict) and isinstance(args.get("workflow_alias"), str):
            aliases.add(args["workflow_alias"])
    return aliases


def _workflow_preset_slugs(definition: BaseModel) -> set[str]:
    preset_slugs: set[str] = set()
    for action in _definition_actions(definition):
        if action.get("action") != "ai.preset_agent":
            continue
        args = action.get("args")
        if isinstance(args, dict) and isinstance(args.get("preset_slug"), str):
            preset_slugs.add(args["preset_slug"])
    return preset_slugs


def _definition_actions(definition: BaseModel) -> list[dict[str, Any]]:
    data = definition.model_dump(mode="json")
    actions = data.get("actions")
    if not isinstance(actions, list):
        return []
    return [action for action in actions if isinstance(action, dict)]


def _find_cycle(graph: dict[str, list[str]]) -> list[str]:
    visiting: set[str] = set()
    visited: set[str] = set()
    path: list[str] = []

    def visit(node: str) -> list[str]:
        if node in visiting:
            cycle_start = path.index(node)
            return path[cycle_start:] + [node]
        if node in visited:
            return []
        visiting.add(node)
        path.append(node)
        for child in sorted(graph.get(node, [])):
            cycle = visit(child)
            if cycle:
                return cycle
        path.pop()
        visiting.remove(node)
        visited.add(node)
        return []

    for node in sorted(graph):
        cycle = visit(node)
        if cycle:
            return cycle
    return []
