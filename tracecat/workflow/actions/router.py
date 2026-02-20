from typing import cast

from fastapi import APIRouter, HTTPException, status
from pydantic import ValidationError

from tracecat.auth.dependencies import WorkspaceUserRole
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatValidationError
from tracecat.identifiers.workflow import AnyWorkflowIDPath, WorkflowUUID
from tracecat.interactions.schemas import ActionInteractionValidator
from tracecat.logger import logger
from tracecat.registry.actions.schemas import RegistryActionInterfaceValidator
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.workflow.actions.dependencies import AnyActionIDPath
from tracecat.workflow.actions.schemas import (
    ActionControlFlow,
    ActionCreate,
    ActionEdge,
    ActionRead,
    ActionReadMinimal,
    ActionUpdate,
    BatchPositionUpdate,
)
from tracecat.workflow.actions.service import WorkflowActionService

router = APIRouter(prefix="/actions", tags=["actions"])


@router.post("/batch-positions", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("workflow:update")
async def batch_update_positions(
    role: WorkspaceUserRole,
    workflow_id: AnyWorkflowIDPath,
    params: BatchPositionUpdate,
    session: AsyncDBSession,
) -> None:
    """Batch update action and trigger positions.

    This endpoint updates all positions in a single transaction for atomicity,
    preventing race conditions from concurrent position updates.
    """
    svc = WorkflowActionService(session, role=role)
    await svc.batch_update_positions(
        workflow_id=workflow_id,
        action_positions=params.actions,
        trigger_position=params.trigger_position,
    )


@router.get("")
@require_scope("workflow:read")
async def list_actions(
    role: WorkspaceUserRole,
    workflow_id: AnyWorkflowIDPath,
    session: AsyncDBSession,
) -> list[ActionReadMinimal]:
    """List all actions for a workflow."""
    svc = WorkflowActionService(session, role=role)
    actions = await svc.list_actions(workflow_id=workflow_id)
    return [
        ActionReadMinimal(
            id=action.id,
            workflow_id=WorkflowUUID.new(action.workflow_id).short(),
            type=action.type,
            title=action.title,
            description=action.description,
            status=action.status,
            is_interactive=action.is_interactive,
        )
        for action in actions
    ]


@router.post("")
@require_scope("workflow:create")
async def create_action(
    role: WorkspaceUserRole,
    params: ActionCreate,
    session: AsyncDBSession,
) -> ActionReadMinimal:
    """Create a new action for a workflow."""
    if role.workspace_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace ID is required"
        )
    svc = WorkflowActionService(session, role=role)
    try:
        action = await svc.create_action(params)
    except TracecatValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Action ref already exists in the workflow",
        ) from e

    return ActionReadMinimal(
        id=action.id,
        workflow_id=WorkflowUUID.new(action.workflow_id).short(),
        type=action.type,
        title=action.title,
        description=action.description,
        status=action.status,
        is_interactive=action.is_interactive,
    )


@router.get("/{action_id}")
@require_scope("workflow:read")
async def get_action(
    role: WorkspaceUserRole,
    action_id: AnyActionIDPath,
    workflow_id: AnyWorkflowIDPath,
    session: AsyncDBSession,
) -> ActionRead:
    """Get an action."""
    svc = WorkflowActionService(session, role=role)
    action = await svc.get_action(action_id=action_id, workflow_id=workflow_id)
    if action is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
        )

    # Add default value for input if it's empty
    if len(action.inputs) == 0:
        # Lookup action type in the registry index (supports both org and platform actions)
        ra_service = RegistryActionsService(session, role=role)
        result = await ra_service.get_action_from_index(action_name=action.type)
        if result is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Action not found in registry",
            )
        manifest_action = result.manifest.actions.get(action.type)
        if manifest_action is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Action not found in manifest",
            )
        # Extract required fields from the interface JSON schema stored in manifest
        # The schema has: { "properties": {...}, "required": [...], ... }
        try:
            interface = RegistryActionInterfaceValidator.validate_python(
                manifest_action.interface
            )
            expects_schema = interface["expects"]
            required_fields = expects_schema.get("required", [])
            # Build prefilled YAML with required fields
            prefilled_inputs = "\n".join(f"{field}: " for field in required_fields)
            action.inputs = prefilled_inputs
        except ValidationError as e:
            logger.warning(
                "Failed to validate registry action interface",
                action_name=action.type,
                error=e,
            )

    return ActionRead(
        id=action.id,
        type=action.type,
        title=action.title,
        description=action.description,
        status=action.status,
        inputs=action.inputs,
        control_flow=ActionControlFlow(**action.control_flow),
        is_interactive=action.is_interactive,
        interaction=(
            ActionInteractionValidator.validate_python(action.interaction)
            if action.interaction is not None
            else None
        ),
        position_x=action.position_x,
        position_y=action.position_y,
        upstream_edges=cast(list[ActionEdge], action.upstream_edges),
    )


@router.post("/{action_id}")
@require_scope("workflow:update")
async def update_action(
    role: WorkspaceUserRole,
    action_id: AnyActionIDPath,
    workflow_id: AnyWorkflowIDPath,
    params: ActionUpdate,
    session: AsyncDBSession,
) -> ActionRead:
    """Update an action."""
    # Fetch the action by id
    svc = WorkflowActionService(session, role=role)
    action = await svc.get_action(action_id=action_id, workflow_id=workflow_id)
    if action is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
        )
    action = await svc.update_action(action, params)
    return ActionRead.model_validate(action)


@router.delete("/{action_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("workflow:delete")
async def delete_action(
    role: WorkspaceUserRole,
    action_id: AnyActionIDPath,
    workflow_id: AnyWorkflowIDPath,
    session: AsyncDBSession,
) -> None:
    """Delete an action."""
    svc = WorkflowActionService(session, role=role)
    action = await svc.get_action(action_id=action_id, workflow_id=workflow_id)
    if action is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
        )
    await svc.delete_action(action)
