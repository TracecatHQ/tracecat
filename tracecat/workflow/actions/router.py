from fastapi import APIRouter, HTTPException, status
from pydantic_core import PydanticUndefined
from sqlalchemy.exc import NoResultFound
from sqlmodel import select

from tracecat.auth.dependencies import WorkspaceUserRole
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.schemas import Action
from tracecat.ee.interactions.models import ActionInteractionValidator
from tracecat.identifiers.action import ActionID
from tracecat.identifiers.workflow import AnyWorkflowIDPath, WorkflowUUID
from tracecat.logger import logger
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.types.exceptions import RegistryError
from tracecat.workflow.actions.models import (
    ActionControlFlow,
    ActionCreate,
    ActionRead,
    ActionReadMinimal,
    ActionUpdate,
)

router = APIRouter(prefix="/actions")


@router.get("", tags=["actions"])
async def list_actions(
    role: WorkspaceUserRole,
    workflow_id: AnyWorkflowIDPath,
    session: AsyncDBSession,
) -> list[ActionReadMinimal]:
    """List all actions for a workflow."""
    statement = select(Action).where(
        Action.owner_id == role.workspace_id,
        Action.workflow_id == workflow_id,
    )
    results = await session.exec(statement)
    actions = results.all()
    response = [
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
    return response


@router.post("", tags=["actions"])
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
    action = Action(
        owner_id=role.workspace_id,
        workflow_id=WorkflowUUID.new(params.workflow_id),
        type=params.type,
        title=params.title,
        description="",  # Default to empty string
        inputs=params.inputs,
        control_flow=params.control_flow.model_dump() if params.control_flow else {},
        is_interactive=params.is_interactive,
        interaction=params.interaction.model_dump() if params.interaction else None,
    )
    # Check if a clashing action ref exists
    statement = select(Action).where(
        Action.owner_id == role.workspace_id,
        Action.workflow_id == action.workflow_id,
        Action.ref == action.ref,
    )
    result = await session.exec(statement)
    if result.first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Action ref already exists in the workflow",
        )

    session.add(action)
    await session.commit()
    await session.refresh(action)

    action_metadata = ActionReadMinimal(
        id=action.id,
        workflow_id=WorkflowUUID.new(action.workflow_id).short(),
        type=action.type,
        title=action.title,
        description=action.description,
        status=action.status,
        is_interactive=action.is_interactive,
    )
    return action_metadata


@router.get("/{action_id}", tags=["actions"])
async def get_action(
    role: WorkspaceUserRole,
    action_id: ActionID,
    workflow_id: AnyWorkflowIDPath,
    session: AsyncDBSession,
) -> ActionRead:
    """Get an action."""
    statement = select(Action).where(
        Action.owner_id == role.workspace_id,
        Action.id == action_id,
        Action.workflow_id == workflow_id,
    )
    result = await session.exec(statement)
    try:
        action = result.one()
    except NoResultFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
        ) from e

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


@router.post("/{action_id}", tags=["actions"])
async def update_action(
    role: WorkspaceUserRole,
    action_id: ActionID,
    params: ActionUpdate,
    session: AsyncDBSession,
) -> ActionRead:
    """Update an action."""
    # Fetch the action by id
    statement = select(Action).where(
        Action.owner_id == role.workspace_id,
        Action.id == action_id,
    )
    result = await session.exec(statement)
    try:
        action = result.one()
    except NoResultFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
        ) from e

    set_fields = params.model_dump(exclude_unset=True)
    for field, value in set_fields.items():
        setattr(action, field, value)
    session.add(action)
    await session.commit()
    await session.refresh(action)

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


@router.delete("/{action_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["actions"])
async def delete_action(
    role: WorkspaceUserRole,
    action_id: ActionID,
    session: AsyncDBSession,
) -> None:
    """Delete an action."""
    statement = select(Action).where(
        Action.owner_id == role.workspace_id,
        Action.id == action_id,
    )
    result = await session.exec(statement)
    try:
        action = result.one()
    except NoResultFound:
        logger.error(f"Action not found: {action_id}. Ignore delete.")
        return
    # If the user doesn't own this workflow, they can't delete the action
    await session.delete(action)
    await session.commit()
