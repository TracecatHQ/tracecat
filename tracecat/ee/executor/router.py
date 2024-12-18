from __future__ import annotations

import traceback

from fastapi import APIRouter, HTTPException, status

from tracecat import config
from tracecat.auth.credentials import RoleACL
from tracecat.contexts import ctx_logger
from tracecat.dsl.models import ActionResult, RunActionInput
from tracecat.ee.store.models import ActionResultHandle
from tracecat.ee.store.service import get_store
from tracecat.executor.enums import ResultsBackend
from tracecat.executor.service import run_action_on_ray_cluster
from tracecat.logger import logger
from tracecat.registry.actions.models import RegistryActionErrorInfo
from tracecat.types.auth import Role

router = APIRouter()


@router.post("/run-store/{action_name}", tags=["execution"])
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
        result = await run_action_on_ray_cluster(input=action_input, role=role)
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
    try:
        action_ref_handle = await store.store_action_result(
            execution_id=action_input.run_context.wf_exec_id,
            action_ref=action_input.task.ref,
            action_result=action_result,
        )
        return action_ref_handle
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
