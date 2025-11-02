from typing import Any

import orjson
from fastapi import APIRouter, HTTPException, status
from pydantic_core import to_jsonable_python

from tracecat import config
from tracecat.auth.credentials import RoleACL
from tracecat.config import TRACECAT__EXECUTOR_PAYLOAD_MAX_SIZE_BYTES
from tracecat.contexts import ctx_logger
from tracecat.db.engine import get_async_engine
from tracecat.dsl.schemas import RunActionInput
from tracecat.executor.schemas import ExecutorActionErrorInfo
from tracecat.executor.service import dispatch_action_on_cluster
from tracecat.logger import logger
from tracecat.types.auth import Role
from tracecat.types.exceptions import (
    ExecutionError,
    LoopExecutionError,
    PayloadSizeExceeded,
    TracecatSettingsError,
)

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
    ref = action_input.task.ref
    log = logger.bind(role=role, action_name=action_name, ref=ref)
    ctx_logger.set(log)

    log.info("Starting action")

    try:
        result = await dispatch_action_on_cluster(input=action_input)
        serialized = orjson.dumps(result, default=to_jsonable_python)
        ser_size = len(serialized)
        if ser_size > TRACECAT__EXECUTOR_PAYLOAD_MAX_SIZE_BYTES:
            raise PayloadSizeExceeded(
                f"The action's return value exceeds the size limit of"
                f" {TRACECAT__EXECUTOR_PAYLOAD_MAX_SIZE_BYTES / 1000}KB"
            )
        return result
    except TracecatSettingsError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"message": str(e)},
        ) from e
    except ExecutionError as e:
        # This is an error that occurred inside an executing action
        err_info_dict = e.info.model_dump(mode="json")
        log.error("Error in action", **err_info_dict)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=err_info_dict,
        ) from e
    except LoopExecutionError as e:
        err_info_list = [e.info.model_dump(mode="json") for e in e.loop_errors]
        log.error("Error in loop", n_errors=len(err_info_list))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=err_info_list,
        ) from e
    except PayloadSizeExceeded as e:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=str(e),
        ) from e
    except orjson.JSONDecodeError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        ) from e
    except Exception as e:
        logger.warning("Unexpected error running action", exc_info=e)
        err_info = ExecutorActionErrorInfo.from_exc(e, action_name)
        err_info_dict = err_info.model_dump(mode="json")
        log.error("Error running action", **err_info_dict)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=err_info_dict,
        ) from e


@router.get("/health/db-pool", tags=["health"])
def get_database_pool_metrics() -> dict[str, Any]:
    """Get SQLAlchemy QueuePool metrics for health monitoring.

    Returns connection pool statistics including:
    - pool_size: Current pool size
    - checked_out: Number of checked out connections
    - checked_in: Number of connections in the pool
    - overflow: Current overflow count
    - status: Formatted pool status string
    """
    try:
        engine = get_async_engine()
        pool = engine.pool

        return {
            "pool_size": pool.size(),  # type: ignore
            "checked_out": pool.checkedout(),  # type: ignore
            "checked_in": pool.checkedin(),  # type: ignore
            "overflow": pool.overflow(),  # type: ignore
            "status": pool.status(),
            "healthy": True,
        }
    except Exception as e:
        logger.error("Error retrieving database pool metrics", exc_info=e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "message": "Failed to retrieve database pool metrics",
                "error": str(e),
            },
        ) from e


if config.TRACECAT__APP_ENV == "development":

    @router.get("/health/db-pool/reset", tags=["health"], include_in_schema=False)
    async def reset_database_pool(
        role: Role = RoleACL(
            allow_user=False,
            allow_service=True,
            require_workspace="no",
        ),
    ) -> dict[str, Any]:
        """Reset/recreate the database connection pool.

        This forcefully disposes of all connections and recreates the pool.
        Use with caution as it will interrupt any active database operations.
        """
        try:
            engine = get_async_engine()

            # Get metrics before reset
            pool = engine.pool
            before_metrics = {
                "pool_size": pool.size(),  # type: ignore
                "checked_out": pool.checkedout(),  # type: ignore
                "overflow": pool.overflow(),  # type: ignore
            }

            # Dispose of all connections in the pool
            # This will close all connections and recreate the pool
            await engine.dispose()
            logger.info("Database pool reset", before=before_metrics)

            # Get new pool metrics after reset
            new_pool = engine.pool
            after_metrics = {
                "pool_size": new_pool.size(),  # type: ignore
                "checked_out": new_pool.checkedout(),  # type: ignore
                "overflow": new_pool.overflow(),  # type: ignore
            }

            return {
                "status": "reset_complete",
                "before": before_metrics,
                "after": after_metrics,
            }
        except Exception as e:
            logger.error("Error resetting database pool", exc_info=e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "message": "Failed to reset database pool",
                    "error": str(e),
                },
            ) from e
