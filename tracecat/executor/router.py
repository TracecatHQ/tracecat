from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from tracecat.auth.credentials import RoleACL
from tracecat.contexts import ctx_logger
from tracecat.db.dependencies import AsyncDBSession
from tracecat.dsl.models import RunActionInput
from tracecat.executor.models import ExecutorActionErrorInfo
from tracecat.executor.service import dispatch_action_on_cluster
from tracecat.logger import logger
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatSettingsError, WrappedExecutionError

router = APIRouter()


@router.post("/run/{action_name}", tags=["execution"])
async def run_action(
    *,
    role: Role = RoleACL(
        allow_user=False,  # XXX(authz): Users cannot execute actions
        allow_service=True,  # Only services can execute actions
        require_workspace="no",
    ),
    session: AsyncDBSession,
    action_name: str,
    action_input: RunActionInput,
) -> Any:
    """Execute a registry action."""
    ref = action_input.task.ref
    act_logger = logger.bind(role=role, action_name=action_name, ref=ref)
    ctx_logger.set(act_logger)

    act_logger.info("Starting action")

    try:
        return await dispatch_action_on_cluster(input=action_input, session=session)
    except TracecatSettingsError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"message": str(e)},
        ) from e
    except WrappedExecutionError as e:
        # This is an error that occurred inside an executing action
        err = e.error
        if isinstance(err, BaseModel):
            err_info_dict = err.model_dump(mode="json")
        else:
            err_info_dict = {"message": str(err)}
        act_logger.error("Error in action", **err_info_dict)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=err_info_dict,
        ) from e
    except Exception as e:
        err_info = ExecutorActionErrorInfo.from_exc(e, action_name)
        err_info_dict = err_info.model_dump(mode="json")
        act_logger.error("Error running action", **err_info_dict)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=err_info_dict,
        ) from e
