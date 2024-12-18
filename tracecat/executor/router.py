import traceback
from typing import Any

from fastapi import APIRouter, HTTPException, status

from tracecat import config
from tracecat.auth.credentials import RoleACL
from tracecat.contexts import ctx_logger, ctx_role
from tracecat.db.dependencies import AsyncDBSession
from tracecat.dsl.models import (
    ActionResult,
    RunActionInput,
)
from tracecat.ee.store.models import ActionResultHandle
from tracecat.ee.store.service import get_store
from tracecat.executor.enums import ResultsBackend
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
        act_logger.error(
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


@router.post("/run-store/{action_name}")
async def run_action_with_store(
    *,
    role: Role = RoleACL(
        allow_user=False,  # XXX(authz): Users cannot execute actions
        allow_service=True,  # Only services can execute actions
        require_workspace="no",
    ),
    action_name: str,
    action_input: RunActionInput,
) -> ActionResultHandle:
    """Execute a registry action."""
    log = logger.bind(role=role, action_name=action_name, ref=action_input.task.ref)
    ctx_logger.set(log)

    # TODO: We should receive action refs and jsonpaths in the input
    # instead of the actual objects themselves.

    log.info("Starting action", backend=config.TRACECAT__RESULTS_BACKEND)
    store = get_store()

    # === Loading dependent action results ===
    # 1. Parse the input args for templated fields
    # 2. Load any dependent action results from the store
    # 3. Update the input args with the resolved values
    # 4. Execute the action

    # This returns the result of the action
    # Lets return a store pointer
    # Start with Storing data in the store first.
    # Then we can return the store pointer
    # ---
    # Regardless of whether we succeed or fail, we should store the result/error
    # in the store so that it can be retrieved later.
    assert (
        config.TRACECAT__RESULTS_BACKEND == ResultsBackend.STORE
    ), "Only store backend is supported n this endpoint"

    action_result = ActionResult()
    try:
        result = await dispatch_action_on_cluster(input=action_input, role=role)
        action_result.update(result=result, result_typename=type(result).__name__)
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

        action_result.update(
            error=error_detail.model_dump(mode="json"),
            error_typename=error_detail.type,
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

    logger.info("Storing action result", action_result=action_result)
    # TODO: Here, we should just return a store pointer to the action result
    # instead of returning the result itself.
    # For now we just return the result itself
    # return result
    action_ref_handle = await store.store_action_result(
        execution_id=action_input.run_context.wf_exec_id,
        action_ref=action_input.task.ref,
        action_result=action_result,
    )
    return action_ref_handle
