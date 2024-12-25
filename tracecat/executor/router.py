from typing import Any

from fastapi import APIRouter, HTTPException, status

from tracecat.auth.credentials import RoleACL
from tracecat.contexts import ctx_logger, ctx_role
from tracecat.db.dependencies import AsyncDBSession
from tracecat.dsl.models import RunActionInput
from tracecat.executor.models import ExecutorSyncInput
from tracecat.executor.service import dispatch_action_on_cluster
from tracecat.logger import logger
from tracecat.registry.actions.models import (
    RegistryActionErrorInfo,
    RegistryActionValidate,
    RegistryActionValidateResponse,
)
from tracecat.registry.repository import RegistryReposService, Repository
from tracecat.types.auth import Role
from tracecat.validation.service import validate_registry_action_args

router = APIRouter()


@router.post("/sync")
async def sync_executor(
    *,
    role: Role = RoleACL(
        allow_user=False,  # XXX(authz): Users cannot sync the executor
        allow_service=True,  # Only services can sync the executor
        require_workspace="no",
    ),
    session: AsyncDBSession,
    input: ExecutorSyncInput,
) -> None:
    """Sync the executor from the registry."""
    rr_service = RegistryReposService(session, role=role)
    db_repo = await rr_service.get_repository_by_id(input.repository_id)
    # If it doesn't exist, do nothing
    if db_repo is None:
        logger.info("Remote repository not found in DB, skipping")
        return
    # If it does exist, sync it
    repo = Repository(db_repo.origin, role=role)
    await repo.load_from_origin(commit_sha=db_repo.commit_sha)


@router.post("/run/{action_name}", tags=["execution"])
async def run_action(
    *,
    role: Role = RoleACL(
        allow_user=False,  # XXX(authz): Users cannot execute actions
        allow_service=True,  # Only services can execute actions
        require_workspace="no",
    ),
    action_name: str,
    action_input: RunActionInput,
) -> Any:
    """Execute a registry action."""
    ref = action_input.task.ref
    ctx_role.set(role)
    act_logger = logger.bind(role=role, action_name=action_name, ref=ref)
    ctx_logger.set(act_logger)

    act_logger.info("Starting action")
    try:
        return await dispatch_action_on_cluster(input=action_input, role=role)
    except Exception as e:
        err_info = RegistryActionErrorInfo.from_exc(e, action_name)
        act_logger.error("Error running action", **err_info.model_dump())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=err_info.model_dump(mode="json"),
        ) from e


@router.post("/validate/{action_name}")
async def validate_action(
    *,
    role: Role = RoleACL(
        allow_user=False,  # XXX(authz): Users cannot validate actions
        allow_service=True,  # Only services can validate actions
        require_workspace="no",
    ),
    session: AsyncDBSession,
    action_name: str,
    params: RegistryActionValidate,
) -> RegistryActionValidateResponse:
    """Validate a registry action."""
    try:
        result = await validate_registry_action_args(
            session=session, action_name=action_name, args=params.args
        )

        if result.status == "error":
            logger.warning(
                "Error validating UDF args", message=result.msg, details=result.detail
            )
        return RegistryActionValidateResponse.from_validation_result(result)
    except KeyError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Action {action_name!r} not found in registry",
        ) from e
