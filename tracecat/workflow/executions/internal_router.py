"""Internal workflow execution router for SDK/UDF access."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

import yaml
from fastapi import APIRouter, HTTPException
from fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field, ValidationError, field_validator
from starlette.status import (
    HTTP_200_OK,
    HTTP_201_CREATED,
    HTTP_204_NO_CONTENT,
    HTTP_400_BAD_REQUEST,
    HTTP_404_NOT_FOUND,
    HTTP_409_CONFLICT,
    HTTP_500_INTERNAL_SERVER_ERROR,
)
from temporalio.client import WorkflowExecutionStatus, WorkflowFailureError
from temporalio.service import RPCError

from tracecat.agent.authoring_context import (
    WorkflowAuthoringContextResponse,
    build_action_contexts,
    build_enabled_models,
    build_secret_hints,
    build_variable_hints,
)
from tracecat.auth.dependencies import ExecutorWorkspaceRole
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.dsl.common import DSLInput
from tracecat.exceptions import (
    BuiltinRegistryHasNoSelectionError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.identifiers import WorkflowExecutionID, WorkflowID
from tracecat.identifiers.workflow import (
    AnyWorkflowID,
    AnyWorkflowIDPath,
    WorkflowUUID,
)
from tracecat.logger import logger
from tracecat.mcp.json_patch import apply_json_patch_operations
from tracecat.mcp.schemas import (
    JsonPatchOperation,
    WorkflowAuthoringContextRequest,
    WorkflowEditDocument,
    WorkflowEditResponse,
)
from tracecat.registry.lock.types import RegistryLock
from tracecat.webhooks import service as webhook_service
from tracecat.webhooks.schemas import WebhookRead, WebhookUpdate
from tracecat.workflow.case_triggers.schemas import (
    CaseTriggerRead,
    CaseTriggerUpdate,
)
from tracecat.workflow.case_triggers.service import CaseTriggersService
from tracecat.workflow.executions.enums import TriggerType
from tracecat.workflow.executions.service import WorkflowExecutionsService
from tracecat.workflow.management.definitions import WorkflowDefinitionsService
from tracecat.workflow.management.draft import (
    WorkflowEditError,
    build_workflow_edit_document,
    compute_workflow_edit_revision,
    normalize_workflow_edit_document_for_persisted_revision,
    parse_workflow_edit_request,
    persist_workflow_edit_document,
    validate_workflow_edit_document,
    validate_workflow_patch_payload,
    workflow_edit_document_changed_sections,
    workflow_edit_document_payload,
)
from tracecat.workflow.management.layout import (
    WorkflowActionLayoutInput,
    auto_generate_layout,
)
from tracecat.workflow.management.management import WorkflowsManagementService
from tracecat.workflow.management.schemas import WorkflowCreate

router = APIRouter(
    prefix="/internal/workflows",
    tags=["internal-workflows"],
    include_in_schema=False,
)


class InternalWorkflowExecuteRequest(BaseModel):
    """Request to execute a workflow."""

    workflow_id: WorkflowID | None = Field(
        default=None, description="Workflow UUID (short or full format)"
    )
    workflow_alias: str | None = Field(
        default=None, description="Workflow alias (alternative to ID)"
    )
    trigger_inputs: Any | None = Field(
        default=None, description="Inputs to pass to the workflow (arbitrary JSON)"
    )
    parent_workflow_execution_id: WorkflowExecutionID | None = Field(
        default=None,
        description="Parent workflow execution ID for correlation (stored in Temporal memo). "
        "Auto-populated by the SDK from context when available.",
    )

    @field_validator("workflow_id", mode="before")
    @classmethod
    def validate_workflow_id(cls, v: AnyWorkflowID | None) -> WorkflowID | None:
        """Convert any valid workflow ID format to WorkflowUUID."""
        if v is None:
            return None
        return WorkflowUUID.new(v)


class InternalWorkflowRunRequest(BaseModel):
    """Request to run a workflow from its draft or a published definition."""

    workflow_id: WorkflowID = Field(
        ..., description="Workflow UUID (short or full format)"
    )
    inputs: Any | None = Field(
        default=None, description="Trigger inputs to pass to the workflow"
    )
    use_draft: bool = Field(
        default=True,
        description="Run the current draft graph (default). Set false to run a "
        "published definition.",
    )
    version: int | None = Field(
        default=None,
        description="Published definition version to run. Only applies when "
        "use_draft is false; null runs the current published version. Ignored "
        "when use_draft is true.",
    )

    @field_validator("workflow_id", mode="before")
    @classmethod
    def validate_workflow_id(cls, v: AnyWorkflowID) -> WorkflowID:
        """Convert any valid workflow ID format to WorkflowUUID."""
        return WorkflowUUID.new(v)


class InternalWorkflowExecuteResponse(BaseModel):
    """Response from workflow execution."""

    workflow_id: WorkflowID = Field(..., description="Workflow ID")
    workflow_execution_id: WorkflowExecutionID = Field(
        ..., description="Workflow execution ID"
    )
    message: str = Field(..., description="Status message")

    @field_validator("workflow_id", mode="before")
    @classmethod
    def validate_workflow_id(cls, v: AnyWorkflowID) -> WorkflowID:
        """Convert any valid workflow ID format to WorkflowUUID."""
        return WorkflowUUID.new(v)


class InternalWorkflowStatusResponse(BaseModel):
    """Response for workflow execution status."""

    workflow_execution_id: WorkflowExecutionID = Field(
        ..., description="Workflow execution ID"
    )
    status: str = Field(
        ...,
        description="Execution status: RUNNING, COMPLETED, FAILED, CANCELED, TERMINATED, TIMED_OUT",
    )
    start_time: datetime | None = Field(
        default=None, description="When the execution started"
    )
    close_time: datetime | None = Field(
        default=None, description="When the execution completed (if finished)"
    )
    result: Any | None = Field(
        default=None, description="Workflow result (if completed successfully)"
    )
    error: str | None = Field(
        default=None, description="Error message (if workflow failed)"
    )


class InternalWorkflowCreateRequest(BaseModel):
    """Request to create a new workflow."""

    title: str | None = Field(
        default=None, description="Workflow title (3-100 characters)"
    )
    description: str | None = Field(
        default=None, description="Optional workflow description"
    )
    definition_yaml: str | None = Field(
        default=None,
        description=(
            "Optional full workflow definition as YAML. When provided, the "
            "workflow is created with these actions instead of being empty."
        ),
    )


class InternalWorkflowCreateResponse(BaseModel):
    """Response from workflow creation."""

    id: WorkflowID = Field(..., description="Workflow ID (short wf_... format)")
    title: str = Field(..., description="Workflow title")

    @field_validator("id", mode="before")
    @classmethod
    def validate_id(cls, v: AnyWorkflowID) -> WorkflowID:
        """Convert any valid workflow ID format to WorkflowUUID."""
        return WorkflowUUID.new(v)


# Top-level keys that mark an already-enveloped workflow YAML payload.
_WORKFLOW_YAML_TOP_LEVEL_KEYS = frozenset(
    {"definition", "layout", "schedules", "case_trigger"}
)


def _build_import_data_from_definition_yaml(
    *,
    definition_yaml: str,
    title: str | None,
    description: str | None,
) -> dict[str, Any]:
    """Parse copilot-supplied workflow YAML into external import data.

    Forgiving normalization so weaker models can one-shot a workflow:

    - Accept either an enveloped payload (top-level ``definition``/``layout``/
      ``schedules``/``case_trigger``) or a bare workflow definition, wrapping the
      latter under ``definition`` (mirrors the MCP create path). A top-level
      ``schedules`` key is tolerated for envelope detection but dropped on import
      (no schedules field); add schedules afterwards via edit-document.
    - Default ``title``/``description`` from the request when omitted.
    - Inject a default ``entrypoint`` when missing. ``DSLInput`` requires the
      key, but infers the actual entrypoint ref from actions with no
      ``depends_on``; supplying ``{"ref": None}`` lets a model that forgets the
      entrypoint still produce a valid workflow.
    - Auto-generate a top-down layout when none is supplied.
    """
    try:
        raw = yaml.safe_load(definition_yaml)
    except yaml.YAMLError as exc:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail=f"Invalid YAML: {exc}",
        ) from exc

    if not isinstance(raw, dict):
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail="Workflow definition YAML must decode to a mapping",
        )
    payload = cast(dict[str, Any], raw)

    # Envelope a bare definition (no top-level definition/layout/... keys).
    if "definition" in payload or _WORKFLOW_YAML_TOP_LEVEL_KEYS.intersection(
        payload.keys()
    ):
        import_data = payload
    else:
        import_data = cast(dict[str, Any], {"definition": payload})

    definition = import_data.get("definition")
    if not isinstance(definition, dict):
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail="Workflow definition must be a mapping",
        )
    if "title" not in definition and title is not None:
        definition["title"] = title
    if "description" not in definition:
        definition["description"] = description or ""
    # Enforce the same title/description constraints the draft edit endpoints
    # apply (WorkflowEditMetadata), so an imported workflow can't later 500 them.
    try:
        WorkflowCreate(
            title=definition.get("title"), description=definition.get("description")
        )
    except ValidationError as exc:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    # DSLInput requires the entrypoint key; let it infer the ref from the graph.
    if "entrypoint" not in definition:
        definition["entrypoint"] = {"ref": None}

    if not import_data.get("layout"):
        actions = definition.get("actions", [])
        if actions:
            # auto_generate_layout indexes each action as a mapping (a["ref"]),
            # so a malformed shape (a mapping instead of a list, or a list of
            # scalars) would raise a raw TypeError/KeyError that escapes the
            # 400-mapping except block in create_workflow and surfaces as a 500.
            # Guard the correctable authoring mistake here as a 400.
            if not isinstance(actions, list) or not all(
                isinstance(action, dict)
                and isinstance(action.get("ref"), str)
                and isinstance(action.get("depends_on") or [], list)
                and all(isinstance(d, str) for d in action.get("depends_on") or [])
                for action in actions
            ):
                raise HTTPException(
                    status_code=HTTP_400_BAD_REQUEST,
                    detail=(
                        "Workflow definition.actions must be a list of action "
                        "objects, each with a string 'ref' and an optional "
                        "'depends_on' list of strings"
                    ),
                )
            import_data["layout"] = auto_generate_layout(
                cast(list[WorkflowActionLayoutInput], actions)
            )
    return import_data


@router.post("", status_code=HTTP_201_CREATED)
@require_scope("workflow:create")
async def create_workflow(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    params: InternalWorkflowCreateRequest,
) -> InternalWorkflowCreateResponse:
    """Create a workflow in the current workspace.

    Empty by default; when ``definition_yaml`` is provided, the workflow is
    created with the supplied actions, layout, and case trigger. Schedules are
    not created here; add them afterwards via the edit-document endpoint.
    """
    service = WorkflowsManagementService(session, role)
    try:
        if params.definition_yaml is not None:
            import_data = _build_import_data_from_definition_yaml(
                definition_yaml=params.definition_yaml,
                title=params.title,
                description=params.description,
            )
            workflow = await service.create_workflow_from_external_definition(
                import_data
            )
        else:
            workflow = await service.create_workflow(
                WorkflowCreate(title=params.title, description=params.description)
            )
    except HTTPException:
        raise
    except (
        BuiltinRegistryHasNoSelectionError,
        TracecatValidationError,
        ValueError,
        ValidationError,
    ) as e:
        # Recoverable client errors -> 400, matching the import router.
        # TracecatValidationError covers raw TracecatDSLError from DSLInput
        # validators (not wrapped as a Pydantic ValidationError).
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    return InternalWorkflowCreateResponse(
        id=WorkflowUUID.new(workflow.id), title=workflow.title
    )


class InternalWorkflowEditDocumentResponse(BaseModel):
    """Editable draft document plus its optimistic-concurrency revision."""

    workflow_id: str = Field(..., description="Workflow ID (short wf_... format)")
    draft_revision: str = Field(
        ..., description="Revision token to pass back as base_revision when editing"
    )
    draft_document: WorkflowEditDocument = Field(
        ..., description="Editable workflow draft (metadata/definition/layout/...)"
    )


class InternalWorkflowEditRequest(BaseModel):
    """Request to edit a workflow draft via RFC 6902 JSON Patch."""

    base_revision: str = Field(
        ..., description="draft_revision returned by GET /{workflow_id}/edit-document"
    )
    patch_ops: list[JsonPatchOperation] = Field(
        ..., description="RFC 6902 JSON Patch operations to apply to the draft"
    )
    validate_only: bool = Field(
        default=False, description="Validate the patch without persisting changes"
    )


def _raise_workflow_edit_http_error(error: WorkflowEditError) -> None:
    """Map a transport-neutral edit error onto an HTTP error.

    Revision conflicts become 409 (carrying ``current_revision`` so the caller
    can refetch and retry); everything else becomes 400 with the same message
    or structured ``details`` payload the engine produced.
    """
    if error.conflict:
        raise HTTPException(
            status_code=HTTP_409_CONFLICT,
            detail={
                "type": "conflict",
                "status": "conflict",
                "message": error.message,
                "current_revision": error.current_revision,
            },
        )
    raise HTTPException(
        status_code=HTTP_400_BAD_REQUEST,
        detail=error.details if error.details is not None else error.message,
    )


@router.get("/{workflow_id}/edit-document", status_code=HTTP_200_OK)
@require_scope("workflow:read")
async def get_workflow_edit_document(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    workflow_id: AnyWorkflowIDPath,
) -> InternalWorkflowEditDocumentResponse:
    """Return a workflow's editable draft document and its revision token."""
    wf_id = workflow_id  # AnyWorkflowIDPath validates the id -> 422 not 500
    service = WorkflowsManagementService(session, role)
    workflow = await service.get_workflow(wf_id)
    if workflow is None:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail=f"Workflow {wf_id} not found",
        )
    try:
        draft_document = build_workflow_edit_document(workflow)
        draft_revision = compute_workflow_edit_revision(draft_document)
    except WorkflowEditError as e:
        _raise_workflow_edit_http_error(e)
        raise  # unreachable; satisfies type checker
    return InternalWorkflowEditDocumentResponse(
        workflow_id=str(workflow.id),
        draft_revision=draft_revision,
        draft_document=draft_document,
    )


@router.patch("/{workflow_id}/edit-document", status_code=HTTP_200_OK)
@require_scope("workflow:update")
async def edit_workflow_document(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    workflow_id: AnyWorkflowIDPath,
    params: InternalWorkflowEditRequest,
) -> WorkflowEditResponse:
    """Apply RFC 6902 JSON Patch operations to a workflow draft.

    Mirrors the MCP ``edit_workflow`` tool, reusing the shared edit-document
    engine so behavior matches exactly. A stale ``base_revision`` yields 409.
    """
    wf_id = workflow_id  # AnyWorkflowIDPath validates the id -> 422 not 500
    service = WorkflowsManagementService(session, role)
    try:
        request = parse_workflow_edit_request(
            base_revision=params.base_revision,
            patch_ops=params.patch_ops,
            validate_only=params.validate_only,
        )

        workflow = await service.get_workflow(wf_id, for_update=True)
        if workflow is None:
            raise HTTPException(
                status_code=HTTP_404_NOT_FOUND,
                detail=f"Workflow {wf_id} not found",
            )

        draft_document = build_workflow_edit_document(workflow)
        current_revision = compute_workflow_edit_revision(draft_document)
        if request.base_revision != current_revision:
            raise WorkflowEditError(
                "Draft revision mismatch",
                conflict=True,
                current_revision=current_revision,
            )

        patched_payload = apply_json_patch_operations(
            document=workflow_edit_document_payload(draft_document),
            patch_ops=request.patch_ops,
        )
        updated_document = validate_workflow_patch_payload(patched_payload)
        changed_sections = workflow_edit_document_changed_sections(
            draft_document,
            updated_document,
        )
        await validate_workflow_edit_document(
            updated_document,
            workflow_id=wf_id,
            existing_layout_action_refs={
                action_layout.ref for action_layout in draft_document.layout.actions
            },
            validate_definition="definition" in changed_sections,
            changed_sections=changed_sections,
            session=service.session,
            role=role,
        )

        if request.validate_only:
            return WorkflowEditResponse(
                message=f"Workflow {wf_id} patch is valid",
                workflow_id=str(workflow.id),
                valid=True,
                validate_only=True,
                draft_revision=compute_workflow_edit_revision(
                    normalize_workflow_edit_document_for_persisted_revision(
                        updated_document
                    )
                ),
            )

        await persist_workflow_edit_document(
            role=role,
            service=service,
            workflow=workflow,
            original_document=draft_document,
            updated_document=updated_document,
        )
        await service.session.refresh(
            workflow,
            ["actions", "schedules", "case_trigger"],
        )
        refreshed_document = build_workflow_edit_document(workflow)
        return WorkflowEditResponse(
            message=f"Workflow {wf_id} updated successfully",
            workflow_id=str(workflow.id),
            draft_revision=compute_workflow_edit_revision(refreshed_document),
        )
    except WorkflowEditError as e:
        _raise_workflow_edit_http_error(e)
        raise  # unreachable; satisfies type checker
    except ToolError as e:
        # apply_json_patch_operations raises ToolError on bad patch application
        # (bad path/index, failed `test`). Mirror the MCP tool: 400 not 500.
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e


class InternalWorkflowPublishResponse(BaseModel):
    """Response from publishing (committing) a workflow."""

    workflow_id: WorkflowID = Field(..., description="Workflow ID")
    version: int = Field(..., description="Newly committed definition version")
    message: str = Field(..., description="Status message")

    @field_validator("workflow_id", mode="before")
    @classmethod
    def validate_workflow_id(cls, v: AnyWorkflowID) -> WorkflowID:
        """Convert any valid workflow ID format to WorkflowUUID."""
        return WorkflowUUID.new(v)


@router.post("/{workflow_id}/publish", status_code=HTTP_201_CREATED)
@require_scope("workflow:update")
async def publish_workflow(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    workflow_id: AnyWorkflowIDPath,
) -> InternalWorkflowPublishResponse:
    """Publish (commit) a workflow's current draft as a new versioned definition.

    Mirrors the MCP publish tool and the public commit route by delegating to the
    shared ``WorkflowsManagementService.publish_workflow``. Correctable draft
    validation problems surface as a 400 carrying the structured error list so the
    caller (e.g. a chat agent) can fix the draft and retry, rather than escaping
    as a 500.
    """
    service = WorkflowsManagementService(session, role=role)
    try:
        result = await service.publish_workflow(workflow_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail=str(e)) from e

    if not result.ok:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail={
                "type": "validation_error",
                "message": f"{len(result.errors)} validation error(s)",
                "errors": [
                    vr.root.model_dump(mode="json", exclude_none=True)
                    for vr in result.errors
                ],
            },
        )

    # result.ok guarantees a non-null version.
    return InternalWorkflowPublishResponse(
        workflow_id=workflow_id,
        version=cast(int, result.version),
        message="Workflow published successfully",
    )


@router.get("/{workflow_id}/webhook", status_code=HTTP_200_OK)
@require_scope("workflow:read")
async def get_webhook(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    workflow_id: AnyWorkflowIDPath,
) -> WebhookRead:
    """Return a workflow's webhook trigger configuration.

    Mirrors the public ``GET /workflows/{id}/webhook`` route so the workflows
    SDK (which resolves under ``/internal``) can read webhook config.
    """
    if role.workspace_id is None:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST, detail="Workspace ID is required"
        )
    webhook = await webhook_service.get_webhook(
        session=session,
        workspace_id=role.workspace_id,
        workflow_id=workflow_id,
    )
    if webhook is None:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Webhook not found")
    return WebhookRead.model_validate(webhook, from_attributes=True)


@router.patch("/{workflow_id}/webhook", status_code=HTTP_204_NO_CONTENT)
@require_scope("workflow:update")
async def update_webhook(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    workflow_id: AnyWorkflowIDPath,
    params: WebhookUpdate,
) -> None:
    """Update a workflow's webhook trigger configuration.

    Mirrors the public ``PATCH /workflows/{id}/webhook`` route so the workflows
    SDK (which resolves under ``/internal``) can enable/disable webhooks.
    """
    if role.workspace_id is None:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST, detail="Workspace ID is required"
        )
    webhook = await webhook_service.get_webhook(
        session=session,
        workspace_id=role.workspace_id,
        workflow_id=workflow_id,
    )
    if webhook is None:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Webhook not found")
    for key, value in params.model_dump(exclude_unset=True).items():
        # Safety: params have been validated by WebhookUpdate
        setattr(webhook, key, value)
    session.add(webhook)
    await session.commit()
    await session.refresh(webhook)


@router.get("/{workflow_id}/case-trigger", status_code=HTTP_200_OK)
@require_scope("workflow:read")
async def get_case_trigger(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    workflow_id: AnyWorkflowIDPath,
) -> CaseTriggerRead:
    """Return a workflow's case-trigger configuration.

    Mirrors the public ``GET /workflows/{id}/case-trigger`` route so the
    workflows SDK (which resolves under ``/internal``) can read case triggers.
    """
    service = CaseTriggersService(session, role=role)
    try:
        case_trigger = await service.get_case_trigger(workflow_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail=str(e)) from e
    return CaseTriggerRead.model_validate(case_trigger, from_attributes=True)


@router.patch("/{workflow_id}/case-trigger", status_code=HTTP_204_NO_CONTENT)
@require_scope("workflow:update")
async def update_case_trigger(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    workflow_id: AnyWorkflowIDPath,
    params: CaseTriggerUpdate,
) -> None:
    """Update a workflow's case-trigger configuration.

    Mirrors the public ``PATCH /workflows/{id}/case-trigger`` route so the
    workflows SDK (which resolves under ``/internal``) can configure case
    triggers. Correctable authoring mistakes (unpublished workflow, online
    with no event types, unknown tag) become 400/404, not 500.
    """
    service = CaseTriggersService(session, role=role)
    try:
        await service.update_case_trigger(workflow_id, params)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail=str(e)) from e
    except TracecatValidationError as e:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail=str(e)) from e


@router.post("/authoring-context", status_code=HTTP_200_OK)
@require_scope("workflow:read")
async def get_authoring_context(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    params: WorkflowAuthoringContextRequest,
) -> WorkflowAuthoringContextResponse:
    """Return authoring context (action schemas, secrets, examples) for actions.

    Surfaced to the chat agent via the ``core.workflow.get_authoring_context``
    registry action so it can write workflow ``args:`` blocks against real action
    schemas instead of guessing. Resolves actions by explicit ``action_names`` or
    by ``query`` search; with neither, returns only the workspace
    variable/secret hints.
    """
    actions = await build_action_contexts(
        role=role,
        action_names=params.action_names,
        query=params.query,
    )
    variable_hints = await build_variable_hints(role=role)
    secret_hints = await build_secret_hints(role=role)
    enabled_models = await build_enabled_models(role=role)
    return WorkflowAuthoringContextResponse(
        actions=actions,
        variable_hints=variable_hints,
        secret_hints=secret_hints,
        enabled_models=enabled_models,
    )


@router.post("/executions", status_code=HTTP_201_CREATED)
@require_scope("workflow:execute")
async def execute_workflow(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    params: InternalWorkflowExecuteRequest,
) -> InternalWorkflowExecuteResponse:
    """Execute a workflow by ID or alias.

    This endpoint is used by the SDK and AI agents to execute workflows
    directly without going through the DSLWorkflow subflow machinery.
    """
    # Resolve workflow ID
    wf_id: WorkflowID | None = None

    if params.workflow_id:
        # Direct workflow ID provided
        wf_id = WorkflowUUID.new(params.workflow_id)
    elif params.workflow_alias:
        # Resolve alias to workflow ID
        wf_service = WorkflowsManagementService(session, role=role)
        wf_id = await wf_service.resolve_workflow_alias(
            params.workflow_alias, use_committed=True
        )
        if wf_id is None:
            raise HTTPException(
                status_code=HTTP_404_NOT_FOUND,
                detail=f"Workflow with alias '{params.workflow_alias}' not found",
            )
    else:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail="Either workflow_id or workflow_alias must be provided",
        )

    # Get workflow definition
    defn_service = WorkflowDefinitionsService(session, role=role)
    defn = await defn_service.get_definition_by_workflow_id(wf_id)
    if defn is None:
        raise HTTPException(
            status_code=HTTP_404_NOT_FOUND,
            detail=f"Workflow definition for '{wf_id}' not found. "
            "The workflow may need to be committed first.",
        )

    # Build DSL from definition content
    try:
        dsl = DSLInput(**defn.content)
    except Exception as e:
        logger.error("Failed to build DSL from definition", error=str(e))
        raise HTTPException(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to build DSL for workflow '{wf_id}': {e}",
        ) from e

    # Start workflow execution
    # This follows the same code path as the regular workflow execution endpoint,
    # including StoredObject handling for trigger inputs (done in _dispatch_workflow)
    try:
        exec_service = await WorkflowExecutionsService.connect(role=role)
        # Pass registry_lock from the definition, same as regular execution
        registry_lock = (
            RegistryLock.model_validate(defn.registry_lock)
            if defn.registry_lock
            else None
        )
        # Build memo for correlation (visible in Temporal UI)
        memo: dict[str, Any] | None = None
        if params.parent_workflow_execution_id:
            memo = {"parent_workflow_execution_id": params.parent_workflow_execution_id}

        response = exec_service.create_workflow_execution_nowait(
            dsl=dsl,
            wf_id=wf_id,
            payload=params.trigger_inputs,
            trigger_type=TriggerType.MANUAL,
            registry_lock=registry_lock,
            memo=memo,
        )
        logger.info(
            "Workflow execution started via internal API",
            workflow_id=wf_id,
            workflow_execution_id=response["wf_exec_id"],
        )
        return InternalWorkflowExecuteResponse(
            workflow_id=response["wf_id"],
            workflow_execution_id=response["wf_exec_id"],
            message=response["message"],
        )
    except Exception as e:
        logger.error("Failed to start workflow execution", error=str(e))
        raise HTTPException(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start workflow execution: {e}",
        ) from e


@router.post("/run", status_code=HTTP_201_CREATED)
@require_scope("workflow:execute")
async def run_workflow(
    *,
    role: ExecutorWorkspaceRole,
    params: InternalWorkflowRunRequest,
) -> InternalWorkflowExecuteResponse:
    """Run a workflow from its draft state or a published definition.

    Shared entry point for the chat SDK's ``core.workflow.run`` tool. Delegates
    to ``WorkflowsManagementService.run_workflow`` so draft-vs-published and
    version selection stay in one place.
    """
    wf_id = WorkflowUUID.new(params.workflow_id)
    try:
        async with WorkflowsManagementService.with_session(role=role) as mgmt_service:
            response = await mgmt_service.run_workflow(
                wf_id,
                inputs=params.inputs,
                use_draft=params.use_draft,
                version=params.version,
            )
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail=str(e)) from e
    except TracecatValidationError as e:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail={
                "type": "TracecatValidationError",
                "message": str(e),
                "detail": e.detail,
            },
        ) from e

    logger.info(
        "Workflow run started via internal API",
        workflow_id=wf_id,
        workflow_execution_id=response["wf_exec_id"],
        use_draft=params.use_draft,
        version=params.version,
    )
    return InternalWorkflowExecuteResponse(
        workflow_id=response["wf_id"],
        workflow_execution_id=response["wf_exec_id"],
        message=response["message"],
    )


@router.get("/executions/{execution_id:path}", status_code=HTTP_200_OK)
@require_scope("workflow:read")
async def get_execution_status(
    *,
    role: ExecutorWorkspaceRole,
    execution_id: WorkflowExecutionID,
) -> InternalWorkflowStatusResponse:
    """Get the status of a workflow execution.

    Returns the current status, timing information, and result (if completed).
    """
    try:
        exec_service = await WorkflowExecutionsService.connect(role=role)
        execution = await exec_service.get_execution(execution_id)

        if execution is None:
            raise HTTPException(
                status_code=HTTP_404_NOT_FOUND,
                detail=f"Workflow execution '{execution_id}' not found",
            )

        # Map Temporal status to string
        status_map = {
            WorkflowExecutionStatus.RUNNING: "RUNNING",
            WorkflowExecutionStatus.COMPLETED: "COMPLETED",
            WorkflowExecutionStatus.FAILED: "FAILED",
            WorkflowExecutionStatus.CANCELED: "CANCELED",
            WorkflowExecutionStatus.TERMINATED: "TERMINATED",
            WorkflowExecutionStatus.CONTINUED_AS_NEW: "CONTINUED_AS_NEW",
            WorkflowExecutionStatus.TIMED_OUT: "TIMED_OUT",
        }
        status = (
            status_map.get(execution.status, "UNKNOWN")
            if execution.status
            else "UNKNOWN"
        )

        # Get result or error based on status
        result = None
        error = None
        if execution.status in (
            WorkflowExecutionStatus.COMPLETED,
            WorkflowExecutionStatus.FAILED,
        ):
            try:
                handle = exec_service.handle(execution_id)
                result = await handle.result()
            except WorkflowFailureError as e:
                # Extract the failure message from the workflow error
                error = WorkflowExecutionsService.format_failure_cause(e.cause)
            except Exception as e:
                logger.warning(
                    "Failed to get workflow result",
                    execution_id=execution_id,
                    error=str(e),
                )

        return InternalWorkflowStatusResponse(
            workflow_execution_id=execution_id,
            status=status,
            start_time=execution.start_time,
            close_time=execution.close_time,
            result=result,
            error=error,
        )

    except RPCError as e:
        if "not found" in str(e).lower():
            raise HTTPException(
                status_code=HTTP_404_NOT_FOUND,
                detail=f"Workflow execution '{execution_id}' not found",
            ) from e
        logger.error("RPC error getting execution status", error=str(e))
        raise HTTPException(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get execution status: {e}",
        ) from e
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get execution status", error=str(e))
        raise HTTPException(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get execution status: {e}",
        ) from e
