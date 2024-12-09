from __future__ import annotations

import traceback
from typing import Any

from fastapi import APIRouter, HTTPException, status

import tracecat.executor.service as executor_service
from tracecat import config
from tracecat.auth.credentials import RoleACL
from tracecat.contexts import ctx_logger
from tracecat.db.dependencies import AsyncDBSession
from tracecat.dsl.models import RunActionInput
from tracecat.executor.enums import ResultsBackend
from tracecat.executor.models import ExecutorSyncInput
from tracecat.logger import logger
from tracecat.registry.actions.models import (
    RegistryActionErrorInfo,
    RegistryActionValidate,
    RegistryActionValidateResponse,
)
from tracecat.registry.repository import Repository
from tracecat.types.auth import Role
from tracecat.types.exceptions import RegistryError
from tracecat.validation.service import validate_registry_action_args

router = APIRouter()


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
    log = logger.bind(role=role, action_name=action_name, ref=action_input.task.ref)
    ctx_logger.set(log)

    log.info("Starting action", backend=config.TRACECAT__RESULTS_BACKEND)
    assert (
        config.TRACECAT__RESULTS_BACKEND == ResultsBackend.MEMORY
    ), "Only memory backend is supported on this endpoint"
    try:
        return await executor_service.run_action_in_pool(input=action_input)
    except Exception as e:
        # Get the traceback info
        tb = traceback.extract_tb(e.__traceback__)[-1]  # Get the last frame
        error_detail = RegistryActionErrorInfo(
            action_name=action_name,
            type=e.__class__.__name__,
            message=str(e),
            filename=tb.filename,
            function=tb.name,
            lineno=tb.lineno,
        )

        log.error(
            "Error running action",
            action_name=action_name,
            type=error_detail.type,
            message=error_detail.message,
            filename=error_detail.filename,
            function=error_detail.function,
            lineno=error_detail.lineno,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error_detail.model_dump(mode="json"),
        ) from e


@router.post("/validate/{action_name}", tags=["validation"])
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


@router.post("/sync", tags=["management"])
async def sync_executor(
    *,
    role: Role = RoleACL(
        allow_user=False,  # XXX(authz): Users cannot sync the executor
        allow_service=True,  # Only services can sync the executor
        require_workspace="no",
    ),
    input: ExecutorSyncInput,
) -> None:
    """Sync the executor from the registry."""
    repo = Repository(origin=input.origin, role=role)
    try:
        await repo.load_from_origin()
    except RegistryError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
        ) from e
