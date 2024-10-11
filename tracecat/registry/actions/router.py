from typing import Any

from fastapi import APIRouter, HTTPException, status
from sqlalchemy.exc import IntegrityError

from tracecat.auth.dependencies import OrgUserOrServiceRole
from tracecat.contexts import ctx_logger
from tracecat.db.dependencies import AsyncDBSession
from tracecat.dsl.models import UDFActionInput
from tracecat.logger import logger
from tracecat.registry import executor
from tracecat.registry.actions.models import (
    RegistryActionCreate,
    RegistryActionRead,
    RegistryActionUpdate,
    RegistryActionValidate,
    RegistryActionValidateResponse,
)
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.registry.constants import DEFAULT_REGISTRY_ORIGIN
from tracecat.types.exceptions import RegistryError
from tracecat.validation import vadliate_registry_action_args

router = APIRouter(prefix="/registry/actions", tags=["registry-actions"])


@router.get("")
async def list_registry_actions(
    role: OrgUserOrServiceRole,
    session: AsyncDBSession,
) -> list[RegistryActionRead]:
    """List all actions in a registry."""
    service = RegistryActionsService(session, role)
    actions = await service.list_actions()
    return [RegistryActionRead.from_database(action) for action in actions]


@router.get("/{action_name}")
async def get_registry_action(
    role: OrgUserOrServiceRole, session: AsyncDBSession, action_name: str
) -> RegistryActionRead:
    """Get a specific registry action."""
    service = RegistryActionsService(session, role)
    try:
        action = await service.get_action(action_name=action_name)
        return RegistryActionRead.from_database(action)
    except RegistryError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_registry_action(
    role: OrgUserOrServiceRole, session: AsyncDBSession, params: RegistryActionCreate
) -> RegistryActionRead:
    """Create a new registry action."""
    service = RegistryActionsService(session, role)
    try:
        action = await service.create_action(params)
        return RegistryActionRead.from_database(action)
    except IntegrityError as e:
        msg = str(e)
        if "duplicate key value" in msg:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=msg) from e
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=msg
        ) from e


@router.patch("/{action_name}", status_code=status.HTTP_204_NO_CONTENT)
async def update_registry_action(
    role: OrgUserOrServiceRole,
    session: AsyncDBSession,
    params: RegistryActionUpdate,
    action_name: str,
) -> None:
    """Update a custom registry action."""
    service = RegistryActionsService(session, role)
    try:
        action = await service.get_action(action_name=action_name)
        await service.update_action(action, params)
    except RegistryError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.delete("/{action_name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_registry_action(
    role: OrgUserOrServiceRole, session: AsyncDBSession, action_name: str
) -> None:
    """Delete a template action."""
    service = RegistryActionsService(session, role)
    try:
        action = await service.get_action(action_name=action_name)
    except RegistryError as e:
        logger.error("Error getting action", action_name=action_name, error=e)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    if action.origin == DEFAULT_REGISTRY_ORIGIN:
        logger.error(
            "Attempted to delete default action",
            action_name=action_name,
            origin=action.origin,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot delete default actions. Please delete the action from your custom registry if you want to remove it.",
        )
    # Delete the action as it's not a base action
    await service.delete_action(action)


# Registry Action Controls


@router.post("/{action_name}/execute")
async def run_registry_action(
    role: OrgUserOrServiceRole, action_name: str, action_input: UDFActionInput
) -> Any:
    """Execute a registry action."""
    ref = action_input.task.ref
    act_logger = logger.bind(role=role, action_name=action_name, ref=ref)
    ctx_logger.set(act_logger)

    act_logger.info("Starting action")
    try:
        return await executor.run_action_from_input(input=action_input)
    except Exception as e:
        act_logger.error("Error running action", action_name=action_name, error=e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        ) from e


@router.post("/{action_name}/validate")
async def validate_registry_action(
    role: OrgUserOrServiceRole,
    session: AsyncDBSession,
    action_name: str,
    params: RegistryActionValidate,
) -> RegistryActionValidateResponse:
    """Validate a registry action."""
    try:
        result = await vadliate_registry_action_args(
            session=session, action_name=action_name, args=params.args
        )

        if result.status == "error":
            logger.error(
                "Error validating UDF args", message=result.msg, details=result.detail
            )
        return RegistryActionValidateResponse.from_validation_result(result)
    except KeyError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Action {action_name!r} not found in registry",
        ) from e
