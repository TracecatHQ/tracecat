"""Workflow resource adapter for workspace VCS sync."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

import yaml
from pydantic import ValidationError
from slugify import slugify

from tracecat.cases.enums import CaseEventType
from tracecat.db.models import Workflow
from tracecat.dsl.common import DSLInput
from tracecat.dsl.enums import PlatformAction
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.sync import PullDiagnostic
from tracecat.workflow.case_triggers.schemas import is_case_trigger_configured
from tracecat.workflow.store.schemas import (
    RemoteCaseTrigger,
    RemoteWebhook,
    RemoteWorkflowDefinition,
    RemoteWorkflowSchedule,
    RemoteWorkflowTag,
    Status,
)
from tracecat.workspace_sync.schemas import (
    WORKFLOW_DEFINITION_FILENAME,
    WORKFLOW_ROOT,
    WorkflowResourceSpec,
)


def workflow_source_path(source_id: str) -> str:
    """Repository path for a workflow's definition file."""
    return f"{WORKFLOW_ROOT}/{source_id}/{WORKFLOW_DEFINITION_FILENAME}"


def workflow_source_id_from_path(
    path: str, *, workflow_root: str = WORKFLOW_ROOT
) -> str | None:
    """Return the source id a workflow definition path maps to, or ``None``.

    Inverse of :func:`workflow_source_path`. ``None`` means ``path`` is not a
    ``<workflow_root>/<source_id>/<definition>`` workflow file.
    """
    parts = path.strip("/").split("/")
    if len(parts) != 3:
        return None
    root, source_id, filename = parts
    if root != workflow_root or filename != WORKFLOW_DEFINITION_FILENAME:
        return None
    return source_id or None


def is_workflow_definition_path(
    path: str, *, workflow_root: str = WORKFLOW_ROOT
) -> bool:
    """Return whether ``path`` is a workflow definition file."""
    return workflow_source_id_from_path(path, workflow_root=workflow_root) is not None


def default_workflow_source_id(*, alias: str | None, title: str) -> str:
    """Derive a slugified source id from a workflow's alias or title.

    Prefers ``alias``, falls back to ``title``, then to ``"workflow"``, and
    trims the result to 96 characters.
    """
    base = (
        slugify((alias or "").strip(), separator="-")
        or slugify(title.strip(), separator="-")
        or "workflow"
    )
    return base[:96].strip("-") or "workflow"


def workflow_spec_from_orm(
    workflow: Workflow,
    *,
    dsl: DSLInput,
    source_id: str,
    include_schedules: bool = False,
) -> WorkflowResourceSpec:
    """Build a :class:`WorkflowResourceSpec` from an ORM workflow and its DSL.

    Carries over the folder path, tags, webhook, and a configured case trigger.
    Schedules are included only when ``include_schedules`` is set.
    """
    folder_path = workflow.folder.path if workflow.folder else None
    webhook = workflow.webhook

    case_trigger = None
    if workflow.case_trigger and is_case_trigger_configured(
        status=workflow.case_trigger.status,
        event_types=workflow.case_trigger.event_types,
        tag_filters=workflow.case_trigger.tag_filters,
    ):
        case_trigger = RemoteCaseTrigger(
            status=cast(Status, workflow.case_trigger.status),
            event_types=[
                CaseEventType(event_type)
                for event_type in workflow.case_trigger.event_types
            ],
            tag_filters=workflow.case_trigger.tag_filters,
        )

    schedules = None
    if include_schedules:
        schedules = [
            RemoteWorkflowSchedule(
                status=cast(Status, s.status),
                cron=s.cron,
                every=s.every,
                offset=s.offset,
                start_at=s.start_at,
                end_at=s.end_at,
                timeout=s.timeout,
            )
            for s in (workflow.schedules or [])
        ] or None

    return WorkflowResourceSpec(
        id=source_id,
        alias=workflow.alias,
        folder_path=folder_path,
        tags=[RemoteWorkflowTag(name=t.name) for t in workflow.tags] or None,
        schedules=schedules,
        webhook=RemoteWebhook(
            methods=webhook.methods,
            status=cast(Status, webhook.status),
            include_headers=webhook.include_headers,
        )
        if webhook
        else None,
        case_trigger=case_trigger,
        definition=dsl,
    )


def workflow_spec_to_remote(
    spec: WorkflowResourceSpec,
    *,
    local_workflow_id: WorkflowUUID,
    local_workflow_ids: Mapping[str, WorkflowUUID] | None = None,
) -> RemoteWorkflowDefinition:
    """Convert a spec into a :class:`RemoteWorkflowDefinition` for local import.

    Stamps the definition with ``local_workflow_id`` and, when
    ``local_workflow_ids`` is supplied, rewrites child-workflow references to the
    local ids via :func:`_definition_with_local_workflow_ids`.
    """
    definition = (
        _definition_with_local_workflow_ids(spec.definition, local_workflow_ids)
        if local_workflow_ids
        else spec.definition
    )
    return RemoteWorkflowDefinition(
        id=local_workflow_id.short(),
        alias=spec.alias,
        folder_path=spec.folder_path,
        tags=spec.tags,
        schedules=spec.schedules,
        webhook=spec.webhook,
        case_trigger=spec.case_trigger,
        definition=definition,
    )


def _definition_with_local_workflow_ids(
    definition: DSLInput,
    local_workflow_ids: Mapping[str, WorkflowUUID],
) -> DSLInput:
    """Rewrite child-workflow references in ``definition`` to local ids.

    Returns the original ``definition`` unchanged when no
    ``CHILD_WORKFLOW_EXECUTE`` action resolves to a known local id.
    """
    normalized_workflow_ids = _normalized_workflow_ids(local_workflow_ids)
    actions = []
    changed = False
    for action in definition.actions:
        if action.action != PlatformAction.CHILD_WORKFLOW_EXECUTE:
            actions.append(action)
            continue
        workflow_id = action.args.get("workflow_id")
        if not isinstance(workflow_id, str):
            actions.append(action)
            continue
        local_id = _local_workflow_id_for_reference(
            workflow_id,
            normalized_workflow_ids,
        )
        if local_id is None:
            actions.append(action)
            continue
        args = {**action.args, "workflow_id": local_id.short()}
        actions.append(action.model_copy(update={"args": args}))
        changed = True
    if not changed:
        return definition
    return definition.model_copy(update={"actions": actions})


def _normalized_workflow_ids(
    local_workflow_ids: Mapping[str, WorkflowUUID],
) -> dict[str, WorkflowUUID]:
    """Index ``local_workflow_ids`` by both source id and short workflow id.

    Lets reference lookups match whether the definition cites a portable source
    id or a short ``WorkflowUUID``.
    """
    normalized: dict[str, WorkflowUUID] = {}
    for source_id, local_id in local_workflow_ids.items():
        normalized[source_id] = local_id
        try:
            normalized[WorkflowUUID.new(source_id).short()] = local_id
        except ValueError:
            continue
    return normalized


def _local_workflow_id_for_reference(
    workflow_id: str,
    local_workflow_ids: Mapping[str, WorkflowUUID],
) -> WorkflowUUID | None:
    """Resolve a child-workflow reference to its local id, or ``None``.

    Tries the raw reference first, then its short ``WorkflowUUID`` form.
    """
    if local_id := local_workflow_ids.get(workflow_id):
        return local_id
    try:
        return local_workflow_ids.get(WorkflowUUID.new(workflow_id).short())
    except ValueError:
        return None


def workflow_spec_from_legacy(
    remote: RemoteWorkflowDefinition,
    *,
    source_id: str | None = None,
) -> WorkflowResourceSpec:
    """Adapt a legacy :class:`RemoteWorkflowDefinition` into a resource spec.

    Uses ``source_id`` as the spec id when given, otherwise the remote's own id.
    """
    return WorkflowResourceSpec(
        id=source_id or remote.id,
        alias=remote.alias,
        folder_path=remote.folder_path,
        tags=remote.tags,
        schedules=remote.schedules,
        webhook=remote.webhook,
        case_trigger=remote.case_trigger,
        definition=remote.definition,
    )


def serialize_workflow_spec(spec: WorkflowResourceSpec) -> str:
    """Serialize a workflow spec to YAML, omitting null fields."""
    data = spec.model_dump(mode="json", exclude_none=True)
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def parse_workflow_spec(
    path: str, content: str, *, workflow_root: str = WORKFLOW_ROOT
) -> tuple[WorkflowResourceSpec | None, PullDiagnostic | None]:
    """Parse a workflow YAML file into a spec, or a :class:`PullDiagnostic`.

    Accepts both the current ``type: workflow`` format and the legacy
    :class:`RemoteWorkflowDefinition` layout. Returns ``(spec, None)`` on
    success or ``(None, diagnostic)`` describing the parse, validation, or
    source-id mismatch failure.
    """
    source_id = workflow_source_id_from_path(path, workflow_root=workflow_root)
    yaml_data: dict[str, Any] | None = None
    try:
        raw = yaml.safe_load(content)
        if not isinstance(raw, dict) or not raw:
            return None, PullDiagnostic(
                workflow_path=path,
                workflow_title=None,
                error_type="parse",
                message="Empty or invalid workflow YAML file",
                details={},
            )
        yaml_data = raw

        if raw.get("type") == "workflow" and raw.get("version") == 1:
            if "id" not in raw and source_id is not None:
                raw = {**raw, "id": source_id}
            spec = WorkflowResourceSpec.model_validate(raw)
            if source_id is not None and spec.id != source_id:
                return None, PullDiagnostic(
                    workflow_path=path,
                    workflow_title=spec.definition.title,
                    error_type="validation",
                    message="Workflow source id does not match its repository path",
                    details={"path_source_id": source_id, "spec_id": spec.id},
                )
            return spec, None

        legacy = RemoteWorkflowDefinition.model_validate(raw)
        return workflow_spec_from_legacy(legacy, source_id=source_id), None
    except yaml.YAMLError as e:
        return None, PullDiagnostic(
            workflow_path=path,
            workflow_title=None,
            error_type="parse",
            message=f"YAML parsing error: {str(e)}",
            details={"yaml_error": str(e)},
        )
    except ValidationError as e:
        workflow_title = (
            yaml_data.get("definition", {}).get("title")
            if isinstance(yaml_data, dict)
            else None
        )
        return None, PullDiagnostic(
            workflow_path=path,
            workflow_title=workflow_title,
            error_type="validation",
            message=f"Validation error: {str(e)}",
            details={"validation_errors": e.errors()},
        )
    except Exception as e:
        return None, PullDiagnostic(
            workflow_path=path,
            workflow_title=None,
            error_type="parse",
            message=f"Unexpected parsing error: {str(e)}",
            details={"error": str(e)},
        )
