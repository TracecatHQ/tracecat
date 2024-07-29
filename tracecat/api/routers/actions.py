from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import NoResultFound
from sqlmodel import Session, select

from tracecat.auth.credentials import authenticate_user
from tracecat.db.engine import get_session
from tracecat.db.schemas import Action
from tracecat.types.api import (
    ActionMetadataResponse,
    ActionResponse,
    CreateActionParams,
    UpdateActionParams,
)
from tracecat.types.auth import Role

router = APIRouter(prefix="/actions")


@router.get("", tags=["actions"])
def list_actions(
    role: Annotated[Role, Depends(authenticate_user)],
    workflow_id: str,
    session: Session = Depends(get_session),
) -> list[ActionMetadataResponse]:
    """List all actions for a workflow."""
    statement = select(Action).where(
        Action.owner_id == role.user_id,
        Action.workflow_id == workflow_id,
    )
    results = session.exec(statement)
    actions = results.all()
    action_metadata = [
        ActionMetadataResponse(
            id=action.id,
            workflow_id=workflow_id,
            type=action.type,
            title=action.title,
            description=action.description,
            status=action.status,
            key=action.key,
        )
        for action in actions
    ]
    return action_metadata


@router.post("", tags=["actions"])
def create_action(
    role: Annotated[Role, Depends(authenticate_user)],
    params: CreateActionParams,
    session: Session = Depends(get_session),
) -> ActionMetadataResponse:
    """Create a new action for a workflow."""
    action = Action(
        owner_id=role.user_id,
        workflow_id=params.workflow_id,
        type=params.type,
        title=params.title,
        description="",  # Default to empty string
    )
    # Check if a clashing action ref exists
    statement = select(Action).where(
        Action.owner_id == role.user_id,
        Action.workflow_id == action.workflow_id,
        Action.ref == action.ref,
    )
    if session.exec(statement).first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Action ref already exists in the workflow",
        )

    session.add(action)
    session.commit()
    session.refresh(action)

    action_metadata = ActionMetadataResponse(
        id=action.id,
        workflow_id=params.workflow_id,
        type=params.type,
        title=action.title,
        description=action.description,
        status=action.status,
        key=action.key,
    )
    return action_metadata


@router.get("/{action_id}", tags=["actions"])
def get_action(
    role: Annotated[Role, Depends(authenticate_user)],
    action_id: str,
    workflow_id: str,
    session: Session = Depends(get_session),
) -> ActionResponse:
    """Get an action."""
    statement = select(Action).where(
        Action.owner_id == role.user_id,
        Action.id == action_id,
        Action.workflow_id == workflow_id,
    )
    result = session.exec(statement)
    try:
        action = result.one()
    except NoResultFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
        ) from e

    return ActionResponse(
        id=action.id,
        type=action.type,
        title=action.title,
        description=action.description,
        status=action.status,
        inputs=action.inputs,
        key=action.key,
        control_flow=action.control_flow,
    )


@router.post("/{action_id}", tags=["actions"])
def update_action(
    role: Annotated[Role, Depends(authenticate_user)],
    action_id: str,
    params: UpdateActionParams,
    session: Session = Depends(get_session),
) -> ActionResponse:
    """Update an action."""
    # Fetch the action by id
    statement = select(Action).where(
        Action.owner_id == role.user_id,
        Action.id == action_id,
    )
    result = session.exec(statement)
    try:
        action = result.one()
    except NoResultFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
        ) from e

    if params.title is not None:
        action.title = params.title
    if params.description is not None:
        action.description = params.description
    if params.status is not None:
        action.status = params.status
    if params.inputs is not None:
        action.inputs = params.inputs
    if params.control_flow is not None:
        action.control_flow = params.control_flow.model_dump(mode="json")

    session.add(action)
    session.commit()
    session.refresh(action)

    return ActionResponse(
        id=action.id,
        type=action.type,
        title=action.title,
        description=action.description,
        status=action.status,
        inputs=action.inputs,
        key=action.key,
    )


@router.delete("/{action_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["actions"])
def delete_action(
    role: Annotated[Role, Depends(authenticate_user)],
    action_id: str,
    session: Session = Depends(get_session),
) -> None:
    """Delete an action."""
    statement = select(Action).where(
        Action.owner_id == role.user_id,
        Action.id == action_id,
    )
    result = session.exec(statement)
    try:
        action = result.one()
    except NoResultFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
        ) from e
    # If the user doesn't own this workflow, they can't delete the action
    session.delete(action)
    session.commit()
