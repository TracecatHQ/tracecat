from fastapi import APIRouter, HTTPException, status
from pydantic_core import PydanticUndefined

from tracecat.auth.dependencies import WorkspaceUserRole
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import RegistryError, TracecatValidationError
from tracecat.identifiers.action import ActionID
from tracecat.identifiers.workflow import AnyWorkflowIDPath, WorkflowUUID
from tracecat.interactions.schemas import ActionInteractionValidator
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.workflow.actions.schemas import (
    ActionControlFlow,
    ActionCreate,
    ActionRead,
    ActionReadMinimal,
    ActionUpdate,
)
from tracecat.workflow.actions.service import WorkflowActionService

router = APIRouter(prefix="/actions", tags=["actions"])


@router.get("")
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
async def get_action(
    role: WorkspaceUserRole,
    action_id: ActionID,
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
        # Lookup action type in the registry
        ra_service = RegistryActionsService(session, role=role)
        try:
            reg_action = await ra_service.load_action_impl(action.type)
        except RegistryError as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Action not found in registry",
            ) from e
        # We want to construct a YAML string that contains the defaults
        prefilled_inputs = "\n".join(
            f"{field}: "
            for field, field_info in reg_action.args_cls.model_fields.items()
            if field_info.default is PydanticUndefined
        )
        action.inputs = prefilled_inputs

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
    )


@router.post("/{action_id}")
async def update_action(
    role: WorkspaceUserRole,
    action_id: ActionID,
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
async def delete_action(
    role: WorkspaceUserRole,
    action_id: ActionID,
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
