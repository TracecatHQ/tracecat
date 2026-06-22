"""Generic resource parsing and validation for workspace sync."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from typing import Any, NamedTuple

import yaml
from pydantic import BaseModel, ValidationError

from tracecat.dsl.common import DSLInput
from tracecat.dsl.enums import PlatformAction
from tracecat.dsl.schemas import ActionStatement
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.sync import PullDiagnostic
from tracecat.workspace_sync.adapters import (
    AGENT_PRESET_RESOURCE_ADAPTER,
    NON_WORKFLOW_RESOURCE_ADAPTERS,
    WORKFLOW_RESOURCE_ADAPTER,
    workspace_spec_from_maps,
)
from tracecat.workspace_sync.enums import SyncResourceType
from tracecat.workspace_sync.schemas import (
    WorkspaceManifest,
    WorkspaceSpec,
)
from tracecat.workspace_sync.workflow import (
    parse_workflow_spec,
    serialize_workflow_spec,
)


def parse_workspace_spec_files(
    files: dict[str, str],
    *,
    manifest: WorkspaceManifest,
) -> tuple[WorkspaceSpec, list[PullDiagnostic]]:
    """Parse manifest-declared resource files into a workspace spec."""
    diagnostics: list[PullDiagnostic] = []
    roots = manifest.resources

    specs_by_attr: dict[str, dict[str, BaseModel]] = {
        WORKFLOW_RESOURCE_ADAPTER.spec_attr: {},
        **{adapter.spec_attr: {} for adapter in NON_WORKFLOW_RESOURCE_ADAPTERS},
    }
    extra_files: dict[SyncResourceType, dict[tuple[str, str], str]] = defaultdict(dict)

    workflow_root = roots.workflows.strip("/")
    for path, content in sorted(files.items()):
        if WORKFLOW_RESOURCE_ADAPTER.source_id_from_path(path, roots) is not None:
            workflow, diagnostic = parse_workflow_spec(
                path,
                content,
                workflow_root=workflow_root,
            )
            if diagnostic is not None:
                diagnostics.append(diagnostic)
            elif workflow is not None:
                specs_by_attr[WORKFLOW_RESOURCE_ADAPTER.spec_attr][workflow.id] = (
                    workflow
                )
            continue

        for adapter in NON_WORKFLOW_RESOURCE_ADAPTERS:
            if source_id := adapter.source_id_from_path(path, roots):
                _parse_yaml_resource(
                    path,
                    content,
                    expected_source_id=source_id,
                    model=adapter.model,
                    destination=specs_by_attr[adapter.spec_attr],
                    diagnostics=diagnostics,
                )
                break

            extra_path = adapter.extra_path_from_path(path, roots)
            if extra_path is None:
                continue
            source_id, relpath = extra_path
            extra_files[adapter.resource_type][(source_id, relpath)] = content
            break

    for adapter in NON_WORKFLOW_RESOURCE_ADAPTERS:
        specs_by_attr[adapter.spec_attr] = adapter.attach_extra_files(
            specs_by_attr[adapter.spec_attr],
            extra_files.get(adapter.resource_type, {}),
            diagnostics,
        )

    spec = workspace_spec_from_maps(specs_by_attr)
    diagnostics.extend(validate_workspace_dependencies(spec))
    return spec, diagnostics


def serialize_workspace_spec_files(
    *,
    manifest: WorkspaceManifest,
    spec: WorkspaceSpec,
    manifest_filename: str,
    manifest_serializer: Callable[[WorkspaceManifest], str],
) -> dict[str, str]:
    """Serialize a workspace spec into its full set of repository files.

    Writes the manifest, each workflow definition, and every non-workflow
    resource's primary file plus companion files, returning a path-sorted map of
    repository path to file content.
    """
    files = {manifest_filename: manifest_serializer(manifest)}
    for source_id, workflow_spec in sorted(spec.workflows.items()):
        files[WORKFLOW_RESOURCE_ADAPTER.source_path(source_id)] = (
            serialize_workflow_spec(workflow_spec)
        )
    for adapter in NON_WORKFLOW_RESOURCE_ADAPTERS:
        for source_id, resource_spec in sorted(adapter.specs(spec).items()):
            files[adapter.source_path(source_id)] = _serialize_yaml_model(resource_spec)
            files.update(adapter.serialize_extra_files(source_id, resource_spec))
    return dict(sorted(files.items()))


def validate_workspace_dependencies(spec: WorkspaceSpec) -> list[PullDiagnostic]:
    """Check cross-resource references in a workspace spec for dangling links.

    Flags workflows that reference unknown child workflow aliases or agent preset
    slugs, agent presets that reference missing skills or subagents, and cyclic
    subagent references. Returns one :class:`PullDiagnostic` per problem found.
    """
    diagnostics: list[PullDiagnostic] = []
    workflow_aliases = {
        workflow.alias for workflow in spec.workflows.values() if workflow.alias
    }
    preset_slugs = {preset.slug for preset in spec.agent_presets.values()}
    skill_slugs = {skill.slug for skill in spec.skills.values()}

    for source_id, workflow in sorted(spec.workflows.items()):
        references = workflow_references(workflow.definition)
        for alias in sorted(references.execute_aliases):
            if alias not in workflow_aliases:
                diagnostics.append(
                    PullDiagnostic(
                        workflow_path=WORKFLOW_RESOURCE_ADAPTER.source_path(source_id),
                        workflow_title=workflow.definition.title,
                        error_type="dependency",
                        message=f"Workflow references missing child workflow alias {alias!r}",
                        details={"workflow_source_id": source_id, "alias": alias},
                    )
                )
        for preset_slug in sorted(references.preset_slugs):
            if preset_slug not in preset_slugs:
                diagnostics.append(
                    PullDiagnostic(
                        workflow_path=WORKFLOW_RESOURCE_ADAPTER.source_path(source_id),
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
                        workflow_path=AGENT_PRESET_RESOURCE_ADAPTER.source_path(
                            source_id
                        ),
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
                        workflow_path=AGENT_PRESET_RESOURCE_ADAPTER.source_path(
                            source_id
                        ),
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


class WorkflowReferences(NamedTuple):
    """Cross-resource references extracted from a workflow definition."""

    execute_aliases: set[str]
    execute_ids: set[WorkflowUUID]
    preset_slugs: set[str]


def workflow_references(definition: DSLInput) -> WorkflowReferences:
    """Collect child workflow and agent preset references in a single pass."""
    execute_aliases: set[str] = set()
    execute_ids: set[WorkflowUUID] = set()
    preset_slugs: set[str] = set()
    for action in definition.actions:
        match action:
            case ActionStatement(
                action=PlatformAction.CHILD_WORKFLOW_EXECUTE, args=args
            ):
                if isinstance(alias := args.get("workflow_alias"), str):
                    execute_aliases.add(alias)
                if isinstance(workflow_id := args.get("workflow_id"), str):
                    try:
                        execute_ids.add(WorkflowUUID.new(workflow_id))
                    except ValueError:
                        pass
            case ActionStatement(
                action=PlatformAction.AI_PRESET_AGENT,
                args={"preset_slug": str(preset_slug)},
            ):
                preset_slugs.add(preset_slug)
    return WorkflowReferences(execute_aliases, execute_ids, preset_slugs)


def _parse_yaml_resource[ModelT: BaseModel](
    path: str,
    content: str,
    *,
    expected_source_id: str,
    model: type[ModelT],
    destination: dict[str, ModelT],
    diagnostics: list[PullDiagnostic],
) -> None:
    """Parse one YAML resource file into ``destination`` or record a diagnostic.

    Loads ``content``, backfills the ``id`` field from ``expected_source_id``
    when absent, validates it against ``model``, and stores the spec under its
    ``source_id``. Any empty file, mismatched id, YAML error, or validation error
    appends a :class:`PullDiagnostic` instead of raising.
    """
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


def _resource_title(data: dict[str, Any] | None) -> str | None:
    """Return a resource's ``name`` or ``title`` for diagnostics, if present."""
    if not data:
        return None
    name = data.get("name")
    if isinstance(name, str):
        return name
    title = data.get("title")
    return title if isinstance(title, str) else None


def _serialize_yaml_model(model: BaseModel) -> str:
    """Serialize a spec model to YAML, omitting null fields and keeping key order."""
    return yaml.safe_dump(
        model.model_dump(mode="json", exclude_none=True),
        sort_keys=False,
        allow_unicode=True,
    )


def _find_cycle(graph: dict[str, list[str]]) -> list[str]:
    """Return the first dependency cycle in ``graph`` as a node path, else ``[]``.

    The returned list repeats the entry node at both ends (e.g.
    ``["a", "b", "a"]``). Nodes and edges are visited in sorted order for
    deterministic results.
    """
    visiting: set[str] = set()
    visited: set[str] = set()
    path: list[str] = []

    def visit(node: str) -> list[str]:
        """Depth-first walk from ``node`` returning a back-edge cycle if found."""
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
