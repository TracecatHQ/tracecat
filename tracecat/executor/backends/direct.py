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
import sys
from contextlib import contextmanager
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
    from collections.abc import Iterator

    from tracecat.auth.types import Role
    from tracecat.dsl.schemas import RunActionInput


@contextmanager
def _temporary_sys_path(paths: list[str]) -> Iterator[None]:
    """Temporarily add paths to sys.path for module imports.

    This is used by DirectBackend to make custom registry modules
    available during in-process execution.
    """
    if not paths:
        yield
        return

    # Add paths to the front of sys.path
    for path in reversed(paths):
        if path not in sys.path:
            sys.path.insert(0, path)
            logger.debug("Added path to sys.path", path=path)

    try:
        yield
    finally:
        # Remove the paths we added
        for path in paths:
            if path in sys.path:
                sys.path.remove(path)
                logger.debug("Removed path from sys.path", path=path)


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
        # Import here to avoid circular dependency
        from tracecat.executor.action_runner import get_action_runner

        action_name = input.task.action
        logger.debug(
            "Executing action directly (no sandbox)",
            action=action_name,
            task_ref=input.task.ref,
        )

        # Get tarball paths from the action runner's cache
        # These were extracted by _get_registry_pythonpath in run_action_on_cluster
        tarball_paths: list[str] = []
        runner = get_action_runner()
        cache_dir = runner.cache_dir
        if cache_dir.exists():
            # Find all extracted tarball directories
            for path in cache_dir.iterdir():
                if path.is_dir() and path.name.startswith("tarball-"):
                    tarball_paths.append(str(path))

        if tarball_paths:
            logger.debug(
                "Adding tarball paths to sys.path for direct execution",
                paths=tarball_paths,
            )

        try:
            with _temporary_sys_path(tarball_paths):
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
