"""Direct in-process executor backend for TESTING ONLY.

This backend executes actions directly in the current process without
any sandbox isolation.

WARNING: This backend provides NO isolation between actions. Actions share the
same process memory space, environment variables, and can affect each other's
state. NEVER use in production - use sandboxed_pool or ephemeral backends instead.

This backend exists solely for:
- Running unit/integration tests without subprocess overhead
- Local development when testing action logic
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
    """Direct in-process execution backend for TESTING ONLY.

    Executes actions directly in the current process without subprocess
    overhead. This provides the fastest execution for tests.

    WARNING: NEVER use in production. This backend provides NO isolation:
    - Actions share the same process memory space
    - Environment variables can leak between actions
    - No resource limits or sandboxing
    - A crash in one action affects the entire worker

    For production use sandboxed_pool (single-tenant) or ephemeral (multi-tenant).
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
