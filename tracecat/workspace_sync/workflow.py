"""Workflow spec conversion and serialization helpers.

This module is deliberately not the workflow ``ResourceAdapter``. It contains
the workflow-specific mechanics that are shared by ``WorkspaceSyncService`` and
the thin workflow adapter shim:

- repository path/source-id helpers
- ORM workflow -> workspace-sync spec conversion
- workspace-sync spec -> workflow-store remote definition conversion
- child-workflow local-id rewriting for imports
- current and legacy workflow YAML parsing/serialization
- splitting Python scripts out into ``scripts/<action ref>.py`` files

The actual adapter class lives in ``tracecat.workspace_sync.adapters.workflow``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

import yaml
from pydantic import ValidationError
from slugify import slugify

from tracecat.cases.enums import CaseEventType
from tracecat.db.models import Form, Workflow
from tracecat.dsl.common import DSLInput
from tracecat.dsl.enums import PlatformAction
from tracecat.dsl.schemas import ActionStatement
from tracecat.forms.service import is_configured, stored_form_config
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.sync import PullDiagnostic, serializable_validation_errors
from tracecat.workflow.case_triggers.schemas import is_case_trigger_configured
from tracecat.workflow.store.schemas import (
    RemoteCaseTrigger,
    RemoteForm,
    RemoteWebhook,
    RemoteWorkflowDefinition,
    RemoteWorkflowSchedule,
    RemoteWorkflowTag,
    Status,
)
from tracecat.workspace_sync.schemas import (
    WorkflowResourceSpec,
)


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
    form: Form | None = None,
) -> WorkflowResourceSpec:
    """Build a :class:`WorkflowResourceSpec` from an ORM workflow and its DSL.

    Carries over the folder path, tags, webhook, a configured case trigger and
    a configured form. ``form`` is passed in because ``Workflow`` has no form
    relationship. Schedules are included only when ``include_schedules`` is set.
    """
    folder_path = workflow.folder.path if workflow.folder else None
    webhook = workflow.webhook

    case_trigger = None
    if workflow.case_trigger and is_case_trigger_configured(
        status=workflow.case_trigger.status,
        event_types=workflow.case_trigger.event_types,
        tag_filters=workflow.case_trigger.tag_filters,
        include_sub_cases=workflow.case_trigger.include_sub_cases,
    ):
        case_trigger = RemoteCaseTrigger(
            status=cast(Status, workflow.case_trigger.status),
            event_types=[
                CaseEventType(event_type)
                for event_type in workflow.case_trigger.event_types
            ],
            tag_filters=workflow.case_trigger.tag_filters,
            include_sub_cases=workflow.case_trigger.include_sub_cases or None,
        )

    remote_form = None
    if form is not None and is_configured(form):
        remote_form = RemoteForm.model_validate(
            {**stored_form_config(form).model_dump(), "status": form.status}
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
        form=remote_form,
        definition=dsl,
    )


def workflow_spec_with_source_workflow_ids(
    spec: WorkflowResourceSpec,
    *,
    source_workflow_ids: Mapping[WorkflowUUID, str],
) -> WorkflowResourceSpec:
    """Rewrite child workflow local ids in ``spec`` to portable source ids."""
    definition = _definition_with_source_workflow_ids(
        spec.definition,
        source_workflow_ids,
    )
    if definition is spec.definition:
        return spec
    return spec.model_copy(update={"definition": definition})


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
        form=spec.form,
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


def _definition_with_source_workflow_ids(
    definition: DSLInput,
    source_workflow_ids: Mapping[WorkflowUUID, str],
) -> DSLInput:
    """Rewrite child-workflow local id references to portable source ids."""
    normalized_source_ids = _normalized_source_workflow_ids(source_workflow_ids)
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
        source_id = _source_workflow_id_for_reference(
            workflow_id,
            normalized_source_ids,
        )
        if source_id is None:
            actions.append(action)
            continue
        args = {**action.args, "workflow_id": source_id}
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


def _normalized_source_workflow_ids(
    source_workflow_ids: Mapping[WorkflowUUID, str],
) -> dict[str, str]:
    """Index source ids by both full and short local workflow ids."""
    normalized: dict[str, str] = {}
    for local_id, source_id in source_workflow_ids.items():
        normalized[str(local_id)] = source_id
        normalized[local_id.short()] = source_id
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


def _source_workflow_id_for_reference(
    workflow_id: str,
    source_workflow_ids: Mapping[str, str],
) -> str | None:
    """Resolve a child-workflow local id reference to its source id."""
    if source_id := source_workflow_ids.get(workflow_id):
        return source_id
    try:
        local_id = WorkflowUUID.new(workflow_id)
    except ValueError:
        return None
    return source_workflow_ids.get(local_id.short()) or source_workflow_ids.get(
        str(local_id)
    )


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
        form=remote.form,
        definition=remote.definition,
    )


WORKFLOW_SCRIPTS_DIR = "scripts"
"""Folder inside a workflow folder that holds its Python scripts."""


def _script_relpath(action: ActionStatement) -> str:
    """Return where an action's script lives inside its workflow folder.

    Refs are unique within a workflow and match ``[a-z0-9_]+``, so they are
    safe, readable file names: ``enrich_ip`` -> ``scripts/enrich_ip.py``.
    """
    return f"{WORKFLOW_SCRIPTS_DIR}/{action.ref}.py"


def _python_script(action: ActionStatement) -> str | None:
    """Return the script a push writes to its own file, or ``None``.

    Only the string ``script`` of a ``core.script.run_python`` action counts.
    """
    if action.action != PlatformAction.RUN_PYTHON:
        return None
    script = action.args.get("script")
    return script if isinstance(script, str) else None


def _with_actions(
    spec: WorkflowResourceSpec, actions: list[ActionStatement]
) -> WorkflowResourceSpec:
    definition = spec.definition.model_copy(update={"actions": actions})
    return spec.model_copy(update={"definition": definition})


def workflow_script_files(spec: WorkflowResourceSpec) -> dict[str, str]:
    """Return each Python script by its path inside the workflow folder.

    These files hold the scripts :func:`serialize_workflow_spec` leaves out of
    ``definition.yml``, so people can read them as plain Python.
    """
    return {
        _script_relpath(action): script
        for action in spec.definition.actions
        if (script := _python_script(action)) is not None
    }


def _workflow_spec_without_scripts(spec: WorkflowResourceSpec) -> WorkflowResourceSpec:
    """Drop every script :func:`workflow_script_files` writes to its own file."""
    actions = [
        action.model_copy(
            update={"args": {k: v for k, v in action.args.items() if k != "script"}}
        )
        if _python_script(action) is not None
        else action
        for action in spec.definition.actions
    ]
    return _with_actions(spec, actions)


def workflow_spec_with_script_files(
    spec: WorkflowResourceSpec, scripts: Mapping[str, str]
) -> tuple[WorkflowResourceSpec, list[str]]:
    """Put each script file back into the action it was split out of.

    ``scripts`` maps paths inside the workflow folder, such as
    ``scripts/enrich_ip.py``, to file contents. An action that still has an
    inline ``script``, as files written before scripts were split out do,
    keeps it.

    Returns:
        The spec, and the script files that belong to no
        ``core.script.run_python`` action. Pushes never leave such a file, so
        it means a hand edit, such as a ref renamed without its file.
    """
    actions = []
    for action in spec.definition.actions:
        script = scripts.get(_script_relpath(action))
        is_python = action.action == PlatformAction.RUN_PYTHON
        if is_python and script is not None and "script" not in action.args:
            action = action.model_copy(
                update={"args": {**action.args, "script": script}}
            )
        actions.append(action)
    python_paths = {
        _script_relpath(action)
        for action in spec.definition.actions
        if action.action == PlatformAction.RUN_PYTHON
    }
    return _with_actions(spec, actions), sorted(scripts.keys() - python_paths)


def serialize_workflow_spec(spec: WorkflowResourceSpec) -> str:
    """Serialize a workflow spec to ``definition.yml`` YAML, omitting null fields.

    Python scripts are left out: :func:`workflow_script_files` writes each one
    to its own file next to ``definition.yml``.
    """
    data = _workflow_spec_without_scripts(spec).model_dump(
        mode="json", exclude_none=True
    )
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def is_current_workflow_spec(raw: Mapping[str, Any]) -> bool:
    """Return whether parsed YAML is the current ``type: workflow`` format.

    Anything else is the legacy format, whose folder name is its id.
    """
    return raw.get("type") == "workflow" and raw.get("version") == 1


def parse_workflow_spec(
    path: str, content: str, *, path_key: str
) -> tuple[WorkflowResourceSpec | None, PullDiagnostic | None]:
    """Parse a workflow YAML file into a spec, or a :class:`PullDiagnostic`.

    Accepts both the current ``type: workflow`` format and the legacy
    :class:`RemoteWorkflowDefinition` layout. A current-format file is
    identified by its ``id``, or by its folder name if it has none. A
    legacy-format file is always identified by its folder name, because its
    in-file ``id`` is a workspace-local workflow id. Returns ``(spec, None)``
    on success or ``(None, diagnostic)`` describing the parse or validation
    failure.
    """
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

        if is_current_workflow_spec(raw):
            if "id" not in raw:
                raw = {**raw, "id": path_key}
            return WorkflowResourceSpec.model_validate(raw), None

        # The legacy format predates readable paths: its folder is the id.
        legacy = RemoteWorkflowDefinition.model_validate(raw)
        return workflow_spec_from_legacy(legacy, source_id=path_key), None
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
            details={
                "validation_errors": serializable_validation_errors(
                    [
                        # A form can hold large inline images: don't echo them.
                        {k: v for k, v in error.items() if k != "input"}
                        if error["loc"][:1] == ("form",)
                        else error
                        for error in e.errors()
                    ]
                )
            },
        )
    except Exception as e:
        return None, PullDiagnostic(
            workflow_path=path,
            workflow_title=None,
            error_type="parse",
            message=f"Unexpected parsing error: {str(e)}",
            details={"error": str(e)},
        )
