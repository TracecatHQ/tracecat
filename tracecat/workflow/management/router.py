import json
from datetime import UTC, datetime
from typing import Literal, cast

import orjson
import yaml
from asyncpg import UniqueViolationError
from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from pydantic import ValidationError
from slugify import slugify
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, NoResultFound

from tracecat.auth.api_keys import generate_api_key
from tracecat.auth.dependencies import WorkspaceUserRole
from tracecat.db.common import DBConstraints
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.models import Webhook, WebhookApiKey, Workflow
from tracecat.dsl.common import DSLInput
from tracecat.dsl.schemas import DSLConfig
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.identifiers.workflow import AnyWorkflowIDPath, WorkflowUUID
from tracecat.logger import logger
from tracecat.pagination import CursorPaginatedResponse, CursorPaginationParams
from tracecat.registry.lock.service import RegistryLockService
from tracecat.settings.service import get_setting
from tracecat.tags.schemas import TagRead
from tracecat.validation.schemas import (
    ValidationDetail,
    ValidationResult,
    ValidationResultType,
)
from tracecat.validation.service import validate_dsl, validate_entrypoint_expects
from tracecat.webhooks import service as webhook_service
from tracecat.webhooks.schemas import (
    WebhookApiKeyGenerateResponse,
    WebhookCreate,
    WebhookRead,
    WebhookUpdate,
)
from tracecat.workflow.actions.schemas import ActionRead
from tracecat.workflow.case_triggers.schemas import (
    CaseTriggerCreate,
    CaseTriggerRead,
    CaseTriggerUpdate,
)
from tracecat.workflow.case_triggers.service import CaseTriggersService
from tracecat.workflow.management.definitions import WorkflowDefinitionsService
from tracecat.workflow.management.folders.service import WorkflowFolderService
from tracecat.workflow.management.management import WorkflowsManagementService
from tracecat.workflow.management.schemas import (
    ExternalWorkflowDefinition,
    WorkflowCommitResponse,
    WorkflowCreate,
    WorkflowDefinitionRead,
    WorkflowDefinitionReadMinimal,
    WorkflowEntrypointValidationRequest,
    WorkflowEntrypointValidationResponse,
    WorkflowMoveToFolder,
    WorkflowRead,
    WorkflowReadMinimal,
    WorkflowUpdate,
)
from tracecat.workflow.management.types import WorkflowDefinitionMinimal
from tracecat.workflow.management.utils import build_trigger_inputs_schema
from tracecat.workflow.schedules.schemas import ScheduleRead

router = APIRouter(prefix="/workflows")


@router.get("", tags=["workflows"])
async def list_workflows(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    filter_tags: list[str] | None = Query(
        default=None,
        description="Filter workflows by tags",
        alias="tag",
    ),
    # limit=0 returns all workflows
    limit: int = Query(default=20, ge=0, le=100),
    cursor: str | None = Query(default=None),
    reverse: bool = Query(default=False),
) -> CursorPaginatedResponse[WorkflowReadMinimal]:
    """List workflows."""
    service = WorkflowsManagementService(session, role=role)

    # Handle limit=0 to return all workflows
    if limit == 0:
        # Fetch all workflows without pagination
        workflows_with_defns = await service.list_workflows(
            tags=filter_tags, reverse=reverse
        )

        # Return unpaginated response
        return CursorPaginatedResponse(
            items=wfs_and_defns_to_response(workflows_with_defns),
            next_cursor=None,
            prev_cursor=None,
            has_more=False,
            has_previous=False,
        )

    # Get paginated workflows
    paginated_response = await service.list_workflows_paginated(
        CursorPaginationParams(limit=limit, cursor=cursor, reverse=reverse),
        tags=filter_tags,
    )

    # Return cursor paginated response with transformed items
    return CursorPaginatedResponse(
        items=wfs_and_defns_to_response(paginated_response.items),
        next_cursor=paginated_response.next_cursor,
        prev_cursor=paginated_response.prev_cursor,
        has_more=paginated_response.has_more,
        has_previous=paginated_response.has_previous,
    )


@router.post("/validate-entrypoint", tags=["workflows"])
async def validate_workflow_entrypoint(
    _role: WorkspaceUserRole,
    payload: WorkflowEntrypointValidationRequest,
) -> WorkflowEntrypointValidationResponse:
    """Validate a workflow entrypoint expects definition."""

    errors = validate_entrypoint_expects(payload.expects)
    validation_results = [ValidationResult.new(err) for err in errors]
    return WorkflowEntrypointValidationResponse(
        valid=len(validation_results) == 0,
        errors=validation_results,
    )


def wfs_and_defns_to_response(
    wfs_and_defns: list[tuple[Workflow, WorkflowDefinitionMinimal | None]],
) -> list[WorkflowReadMinimal]:
    res = []
    for workflow, defn in wfs_and_defns:
        tags = [
            TagRead.model_validate(tag, from_attributes=True) for tag in workflow.tags
        ]
        latest_defn = (
            WorkflowDefinitionReadMinimal.model_validate(defn, from_attributes=True)
            if defn
            else None
        )
        res.append(
            WorkflowReadMinimal(
                id=WorkflowUUID.new(workflow.id).short(),
                title=workflow.title,
                description=workflow.description,
                status=workflow.status,
                icon_url=workflow.icon_url,
                created_at=workflow.created_at,
                updated_at=workflow.updated_at,
                version=workflow.version,
                tags=tags,
                alias=workflow.alias,
                error_handler=workflow.error_handler,
                latest_definition=latest_defn,
                folder_id=workflow.folder_id,
            )
        )
    return res


@router.post("", status_code=status.HTTP_201_CREATED, tags=["workflows"])
async def create_workflow(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    title: str | None = Form(default=None, min_length=1, max_length=100),
    description: str | None = Form(default=None, max_length=1000),
    use_workflow_id: bool = Form(
        default=False,
        description="Use the workflow ID if it is provided in the YAML file",
    ),
    file: UploadFile | None = File(default=None),
) -> WorkflowReadMinimal:
    """Create a new Workflow.

    Optionally, you can provide a YAML file to create a workflow.
    You can also provide a title and description to create a blank workflow."""

    service = WorkflowsManagementService(session, role=role)
    if file:
        raw_data = await file.read()
        match file.content_type:
            case (
                "application/yaml"
                | "text/yaml"
                | "application/x-yaml"
                | "application/octet-stream"
            ):
                logger.info("Parsing YAML file", file=file.filename)
                try:
                    external_defn_data = yaml.safe_load(raw_data)
                except yaml.YAMLError as e:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Error parsing YAML file: {e!r}",
                    ) from e
            case "application/json":
                logger.info("Parsing JSON file", file=file.filename)
                try:
                    external_defn_data = orjson.loads(raw_data)
                except orjson.JSONDecodeError as e:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Error parsing JSON file: {e!r}",
                    ) from e
            case None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Content-Type header is required for file uploads.",
                )
            case _:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid file type {file.content_type}. Only YAML and JSON files are supported.",
                )

        logger.info("Importing workflow", external_defn_data=external_defn_data)
        try:
            workflow = await service.create_workflow_from_external_definition(
                external_defn_data, use_workflow_id=use_workflow_id
            )
        except ValidationError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=json.dumps(
                    {
                        "status": "failure",
                        "message": "Error validating external workflow definition",
                        "errors": e.errors(),
                    },
                    indent=2,
                ),
            ) from e
        except IntegrityError as e:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Workflow already exists",
            ) from e
    else:
        workflow = await service.create_workflow(
            WorkflowCreate(title=title, description=description)
        )
    return WorkflowReadMinimal(
        id=WorkflowUUID.new(workflow.id).short(),
        title=workflow.title,
        description=workflow.description,
        status=workflow.status,
        icon_url=workflow.icon_url,
        created_at=workflow.created_at,
        updated_at=workflow.updated_at,
        version=workflow.version,
        error_handler=workflow.error_handler,
    )


@router.get("/{workflow_id}", tags=["workflows"])
async def get_workflow(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    workflow_id: AnyWorkflowIDPath,
) -> WorkflowRead:
    """Return Workflow as title, description, list of Action JSONs, adjacency list of Action IDs."""
    # Get Workflow given workflow_id
    service = WorkflowsManagementService(session, role=role)
    workflow = await service.get_workflow(workflow_id)
    if not workflow:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found"
        )

    actions = workflow.actions or []
    actions_responses = {
        str(action.id): ActionRead.model_validate(action, from_attributes=True)
        for action in actions
    }
    # Add webhook/schedules
    try:
        expects_schema = build_trigger_inputs_schema(workflow.expects)
    except Exception as e:
        logger.warning(
            "Failed to build trigger inputs schema, falling back to None", error=e
        )
        expects_schema = None
    return WorkflowRead(
        id=WorkflowUUID.new(workflow.id).short(),
        workspace_id=workflow.workspace_id,
        title=workflow.title,
        description=workflow.description,
        status=workflow.status,
        version=workflow.version,
        expects=workflow.expects,
        expects_schema=expects_schema,
        returns=workflow.returns,
        entrypoint=workflow.entrypoint,
        config=DSLConfig(**workflow.config),
        actions=actions_responses,
        webhook=WebhookRead.model_validate(workflow.webhook, from_attributes=True),
        schedules=ScheduleRead.list_adapter().validate_python(workflow.schedules),
        alias=workflow.alias,
        error_handler=workflow.error_handler,
        trigger_position_x=workflow.trigger_position_x,
        trigger_position_y=workflow.trigger_position_y,
        graph_version=workflow.graph_version,
    )


@router.patch(
    "/{workflow_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["workflows"],
)
async def update_workflow(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    workflow_id: AnyWorkflowIDPath,
    params: WorkflowUpdate,
) -> None:
    """Update a workflow."""
    service = WorkflowsManagementService(session, role=role)
    try:
        await service.update_workflow(workflow_id, params=params)
    except NoResultFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
        ) from e
    except IntegrityError as e:
        while cause := e.__cause__:
            e = cause
        if isinstance(
            e, UniqueViolationError
        ) and DBConstraints.WORKFLOW_ALIAS_UNIQUE_IN_WORKSPACE in str(e):
            logger.warning("Unique violation error", error=e)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=DBConstraints.WORKFLOW_ALIAS_UNIQUE_IN_WORKSPACE.msg(),
            ) from e
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Workflow already exists",
        ) from e


@router.delete(
    "/{workflow_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["workflows"],
)
async def delete_workflow(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    workflow_id: AnyWorkflowIDPath,
) -> None:
    """Delete a workflow."""

    service = WorkflowsManagementService(session, role=role)
    try:
        await service.delete_workflow(workflow_id)
    except NoResultFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
        ) from e


@router.post("/{workflow_id}/commit", tags=["workflows"])
async def commit_workflow(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    workflow_id: AnyWorkflowIDPath,
) -> WorkflowCommitResponse:
    """Commit a workflow.

    This deploys the workflow and updates its version. If a YAML file is provided, it will override the workflow in the database."""

    # XXX: This is actually the logical equivalent of creating a workflow definition (deployment)
    # Committing from YAML (i.e. attaching yaml) will override the workflow definition in the database

    mgmt_service = WorkflowsManagementService(session, role=role)
    workflow = await mgmt_service.get_workflow(workflow_id)
    if not workflow:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Could not find workflow"
        )

    # Perform Tiered Validation
    # Tier 1: DSLInput validation
    # Verify that the workflow DSL is structurally sound
    construction_errors: list[ValidationResult] = []
    dsl: DSLInput | None = None
    try:
        # Convert the workflow into a WorkflowDefinition
        # XXX: When we commit from the workflow, we have action IDs
        dsl = await mgmt_service.build_dsl_from_workflow(workflow)
    except TracecatValidationError as e:
        logger.info("Custom validation error in DSL", e=e)
        construction_errors.append(
            ValidationResult.new(
                type=ValidationResultType.DSL,
                status="error",
                msg=str(e),
                detail=e.detail,
            )
        )
    except ValidationError as e:
        logger.info("Pydantic validation error in DSL", e=e)
        construction_errors.append(
            ValidationResult.new(
                type=ValidationResultType.DSL,
                status="error",
                msg=str(e),
                detail=ValidationDetail.list_from_pydantic(e),
            )
        )

    if construction_errors:
        return WorkflowCommitResponse(
            workflow_id=workflow_id.short(),
            status="failure",
            message=f"Workflow definition construction failed with {len(construction_errors)} errors",
            errors=construction_errors,
        )

    if dsl is None:
        raise ValueError("dsl should be defined if no construction errors")
    # When we're here, we've verified that the workflow DSL is structurally sound
    # Now, we have to ensure that the arguments are sound

    if val_errors := await validate_dsl(session=session, dsl=dsl, role=role):
        logger.info("Validation errors", errors=val_errors)
        return WorkflowCommitResponse(
            workflow_id=workflow_id.short(),
            status="failure",
            message=f"{len(val_errors)} validation error(s)",
            errors=list(val_errors),
        )

    # Validation is complete. We can now construct the workflow definition
    # Phase 1: Create workflow definition
    # Workflow definition uses action.refs to refer to actions
    # We should only instantiate action refs at workflow runtime
    service = WorkflowDefinitionsService(session, role=role)

    # Always resolve registry_lock from current DSL actions
    # This ensures the lock reflects the actual actions in the workflow, not stale data
    lock_service = RegistryLockService(session, role)
    action_names = {action.action for action in dsl.actions}
    registry_lock = await lock_service.resolve_lock_with_bindings(action_names)
    # Update the workflow with the newly computed lock
    workflow.registry_lock = registry_lock.model_dump()

    # Creating a workflow definition only uses refs
    # Copy the alias from the draft workflow to the committed definition
    # Pass the registry_lock to freeze it with this definition
    defn = await service.create_workflow_definition(
        workflow_id,
        dsl,
        alias=workflow.alias,
        registry_lock=registry_lock,
        commit=False,
    )

    # Update Workflow
    # We don't need to backpropagate the graph to the workflow beacuse the workflow is the source of truth
    # We only need to update the workflow definition version
    workflow.version = defn.version

    session.add(workflow)
    session.add(defn)
    await session.commit()
    await session.refresh(workflow)
    await session.refresh(defn)

    return WorkflowCommitResponse(
        workflow_id=workflow_id.short(),
        status="success",
        message="Workflow committed successfully.",
        metadata={"version": defn.version},
    )


@router.get("/{workflow_id}/export", tags=["workflows"])
async def export_workflow(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    workflow_id: AnyWorkflowIDPath,
    format: Literal["json", "yaml"] = Query(
        default="json", description="Export format: 'json' or 'yaml'"
    ),
    version: int | None = Query(
        default=None,
        description="Workflow definition version. If not provided, the latest version is exported.",
    ),
    draft: bool = Query(
        default=False,
        description="Export current draft state instead of saved definition.",
    ),
):
    """
    Export a workflow's current state and optionally its definitions and logs.

    Supported formats are JSON and YAML.

    When `draft=True`, exports the current draft state from the workflow canvas
    without requiring a saved definition. When `draft=False` (default), exports
    from a saved workflow definition version.
    """
    # Check if workflow exports are enabled
    if not await get_setting(
        "app_workflow_export_enabled", session=session, default=True
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Workflow exports are disabled",
        )

    logger.info(
        "Exporting workflow",
        workflow_id=workflow_id,
        format=format,
        version=version,
        draft=draft,
    )

    if draft:
        # Build DSL from current workflow state (no saved definition required)
        mgmt_service = WorkflowsManagementService(session, role=role)
        workflow = await mgmt_service.get_workflow(workflow_id)
        if not workflow:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Workflow not found",
            )
        try:
            dsl = await mgmt_service.build_dsl_from_workflow(workflow)
        except TracecatValidationError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot export draft: {e}",
            ) from e
        except ValidationError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot export draft: {e}",
            ) from e
        external_defn = ExternalWorkflowDefinition(
            workspace_id=workflow.workspace_id,
            workflow_id=WorkflowUUID.new(workflow.id),
            definition=dsl,
            case_trigger=workflow.case_trigger
            and {
                "status": workflow.case_trigger.status,
                "event_types": workflow.case_trigger.event_types,
                "tag_filters": workflow.case_trigger.tag_filters,
            },
        )
    else:
        # Existing behavior: fetch from WorkflowDefinition
        service = WorkflowDefinitionsService(session, role=role)
        defn = await service.get_definition_by_workflow_id(workflow_id, version=version)
        if not defn:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Workflow definition not found",
            )
        external_defn = ExternalWorkflowDefinition.from_database(defn)
    filename = f"{external_defn.workflow_id}__{slugify(external_defn.definition.title)}.{format}"
    if format == "json":
        return Response(
            content=external_defn.model_dump_json(indent=2, exclude_none=True),
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    elif format == "yaml":
        return Response(
            content=yaml.dump(
                external_defn.model_dump(mode="json", exclude_none=True), indent=2
            ),
            media_type="application/yaml",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{format!r} is not a supported export format",
        )


# ----- Workflow Definitione ----- #


@router.get("/{workflow_id}/definitions", tags=["workflows"])
async def list_workflow_definitions(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    workflow_id: AnyWorkflowIDPath,
) -> list[WorkflowDefinitionRead]:
    """List all workflow definitions for a Workflow."""
    service = WorkflowDefinitionsService(session, role=role)
    defns = await service.list_workflow_defitinions(workflow_id=workflow_id)
    return WorkflowDefinitionRead.list_adapter().validate_python(defns)


@router.get("/{workflow_id}/definition", tags=["workflows"])
async def get_workflow_definition(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    workflow_id: AnyWorkflowIDPath,
    version: int | None = None,
) -> WorkflowDefinitionRead:
    """Get the latest version of a workflow definition."""
    service = WorkflowDefinitionsService(session, role=role)
    definition = await service.get_definition_by_workflow_id(
        workflow_id, version=version
    )
    if definition is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
        )
    return WorkflowDefinitionRead.model_validate(definition)


@router.post("/{workflow_id}/definition", tags=["workflows"])
async def create_workflow_definition(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    workflow_id: AnyWorkflowIDPath,
) -> WorkflowDefinitionRead:
    """Get the latest version of a workflow definition."""
    raise NotImplementedError


# ----- Workflow Webhooks ----- #


@router.post(
    "/{workflow_id}/webhook",
    status_code=status.HTTP_201_CREATED,
    tags=["triggers"],
)
async def create_webhook(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    workflow_id: AnyWorkflowIDPath,
    params: WebhookCreate,
) -> None:
    """Create a webhook for a workflow."""
    if role.workspace_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace ID is required"
        )

    webhook = Webhook(
        workspace_id=role.workspace_id,
        methods=cast(list[str], params.methods),
        workflow_id=workflow_id,
        status=params.status,
        allowlisted_cidrs=params.allowlisted_cidrs,
    )
    session.add(webhook)
    await session.commit()
    await session.refresh(webhook)


@router.get(
    "/{workflow_id}/webhook",
    tags=["triggers"],
)
async def get_webhook(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    workflow_id: AnyWorkflowIDPath,
) -> WebhookRead:
    """Get the webhook from a workflow."""
    if role.workspace_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace ID is required"
        )
    webhook = await webhook_service.get_webhook(
        session=session,
        workspace_id=role.workspace_id,
        workflow_id=workflow_id,
    )
    if webhook is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found"
        )
    return WebhookRead.model_validate(webhook, from_attributes=True)


@router.patch(
    "/{workflow_id}/webhook",
    tags=["triggers"],
    status_code=status.HTTP_204_NO_CONTENT,
)
async def update_webhook(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    workflow_id: AnyWorkflowIDPath,
    params: WebhookUpdate,
) -> None:
    """Update the webhook for a workflow. We currently supprt only one webhook per workflow."""
    result = await session.execute(
        select(Workflow).where(
            Workflow.workspace_id == role.workspace_id,
            Workflow.id == workflow_id,
        )
    )
    try:
        workflow = result.scalar_one()
    except NoResultFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
        ) from e

    webhook = workflow.webhook

    for key, value in params.model_dump(exclude_unset=True).items():
        # Safety: params have been validated
        setattr(webhook, key, value)

    session.add(webhook)
    await session.commit()
    await session.refresh(webhook)


# ----- Workflow Case Triggers ----- #


@router.post(
    "/{workflow_id}/case-trigger",
    status_code=status.HTTP_201_CREATED,
    tags=["triggers"],
    response_model=CaseTriggerRead,
)
async def create_case_trigger(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    workflow_id: AnyWorkflowIDPath,
    params: CaseTriggerCreate,
) -> CaseTriggerRead:
    """Create or replace the case trigger configuration for a workflow."""
    service = CaseTriggersService(session, role=role)
    try:
        case_trigger = await service.upsert_case_trigger(workflow_id, params)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(e)
        ) from e
    except TracecatValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
    return CaseTriggerRead.model_validate(case_trigger, from_attributes=True)


@router.get(
    "/{workflow_id}/case-trigger",
    tags=["triggers"],
    response_model=CaseTriggerRead,
)
async def get_case_trigger(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    workflow_id: AnyWorkflowIDPath,
) -> CaseTriggerRead:
    """Get the case trigger configuration for a workflow."""
    service = CaseTriggersService(session, role=role)
    try:
        case_trigger = await service.get_case_trigger(workflow_id)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(e)
        ) from e
    return CaseTriggerRead.model_validate(case_trigger, from_attributes=True)


@router.patch(
    "/{workflow_id}/case-trigger",
    tags=["triggers"],
    status_code=status.HTTP_204_NO_CONTENT,
)
async def update_case_trigger(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    workflow_id: AnyWorkflowIDPath,
    params: CaseTriggerUpdate,
) -> None:
    """Update the case trigger configuration for a workflow."""
    service = CaseTriggersService(session, role=role)
    try:
        await service.update_case_trigger(workflow_id, params)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(e)
        ) from e
    except TracecatValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e


@router.post(
    "/{workflow_id}/webhook/api-key",
    tags=["triggers"],
    status_code=status.HTTP_201_CREATED,
)
async def generate_webhook_api_key(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    workflow_id: AnyWorkflowIDPath,
) -> WebhookApiKeyGenerateResponse:
    """Create or rotate the API key for a webhook."""
    if role.workspace_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace ID is required"
        )
    webhook = await webhook_service.get_webhook(
        session=session, workspace_id=role.workspace_id, workflow_id=workflow_id
    )
    if webhook is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found"
        )
    now = datetime.now(UTC)
    generated = generate_api_key()
    preview = generated.preview()
    api_key = webhook.api_key
    if api_key is None:
        api_key = WebhookApiKey(
            workspace_id=role.workspace_id,
            webhook_id=webhook.id,
            hashed=generated.hashed,
            salt=generated.salt_b64,
            preview=preview,
        )
    else:
        api_key.workspace_id = role.workspace_id
        api_key.hashed = generated.hashed
        api_key.salt = generated.salt_b64
        api_key.preview = preview
        api_key.last_used_at = None
        api_key.revoked_at = None
        api_key.revoked_by = None
    api_key.created_at = now
    api_key.updated_at = now
    session.add(api_key)
    await session.commit()
    return WebhookApiKeyGenerateResponse(
        api_key=generated.raw,
        preview=preview,
        created_at=now,
    )


@router.post(
    "/{workflow_id}/webhook/api-key/revoke",
    tags=["triggers"],
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_webhook_api_key(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    workflow_id: AnyWorkflowIDPath,
) -> None:
    """Revoke the current API key for a webhook."""
    if role.workspace_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace ID is required"
        )
    webhook = await webhook_service.get_webhook(
        session=session, workspace_id=role.workspace_id, workflow_id=workflow_id
    )
    if webhook is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found"
        )
    now = datetime.now(UTC)
    api_key = webhook.api_key
    if api_key is None:
        return
    if api_key.revoked_at is not None:
        return
    api_key.revoked_at = now
    api_key.revoked_by = role.user_id
    api_key.updated_at = now
    session.add(api_key)
    await session.commit()


@router.delete(
    "/{workflow_id}/webhook/api-key",
    tags=["triggers"],
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_webhook_api_key(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    workflow_id: AnyWorkflowIDPath,
) -> None:
    """Delete the current API key for a webhook."""
    if role.workspace_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace ID is required"
        )
    webhook = await webhook_service.get_webhook(
        session=session, workspace_id=role.workspace_id, workflow_id=workflow_id
    )
    if webhook is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found"
        )
    api_key = webhook.api_key
    if api_key is None:
        return
    await session.delete(api_key)
    webhook.api_key = None
    await session.commit()


@router.post(
    "/{workflow_id}/move", tags=["workflows"], status_code=status.HTTP_204_NO_CONTENT
)
async def move_workflow_to_folder(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    workflow_id: AnyWorkflowIDPath,
    params: WorkflowMoveToFolder,
) -> None:
    """Move a workflow to a different folder.

    If folder_id is null, the workflow will be moved to the root (no folder).
    """
    service = WorkflowFolderService(session, role=role)

    # Verify the folder exists if provided
    path = params.folder_path
    if path is not None and path != "/":
        folder = await service.get_folder_by_path(path)
        if not folder:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Folder not found"
            )
    else:
        folder = None

    try:
        await service.move_workflow(workflow_id, folder)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
