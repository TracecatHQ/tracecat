"""Direct in-process executor backend.

This backend executes actions directly in the current process without
any sandbox isolation. It is intended for development and testing only.

WARNING: Do not use in production with untrusted workloads.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from tracecat.executor.backends.base import ExecutorBackend
from tracecat.executor.schemas import (
    ExecutorActionErrorInfo,
    ExecutorResult,
    ExecutorResultFailure,
    ExecutorResultSuccess,
)
from tracecat.executor.service import run_action_from_input
from tracecat.logger import logger

if TYPE_CHECKING:
    from tracecat.auth.types import Role
    from tracecat.dsl.schemas import RunActionInput


class DirectBackend(ExecutorBackend):
    """Direct in-process execution backend.

    Executes actions directly in the current process without any
    sandbox isolation. Fastest option but provides no security isolation.

    Use cases:
    - Local development
    - Testing
    - Trusted single-tenant environments

    WARNING: Not suitable for production multitenant deployments.
    """

    async def execute(
        self,
        input: RunActionInput,
        role: Role,
        timeout: float = 300.0,
    ) -> ExecutorResult:
        """Execute action directly in-process."""

        action_name = input.task.action
        logger.debug(
            "Executing action directly (no sandbox)",
            action=action_name,
            task_ref=input.task.ref,
        )

        try:
            result = await asyncio.wait_for(
                run_action_from_input(input, role),
                timeout=timeout,
            )
            return ExecutorResultSuccess(result=result)
        except TimeoutError:
            logger.error(
                "Direct execution timed out",
                action=action_name,
                timeout=timeout,
            )
            error_info = ExecutorActionErrorInfo(
                action_name=action_name,
                type="TimeoutError",
                message=f"Action execution timed out after {timeout}s",
                filename="direct.py",
                function="execute",
            )
            return ExecutorResultFailure(error=error_info)
        except Exception as e:
            logger.error(
                "Direct execution failed",
                action=action_name,
                error=str(e),
                error_type=type(e).__name__,
            )
            error_info = ExecutorActionErrorInfo.from_exc(e, action_name=action_name)
            return ExecutorResultFailure(error=error_info)
