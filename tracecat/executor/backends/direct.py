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
from typing import TYPE_CHECKING, Any

from tracecat_registry import secrets as registry_secrets
from tracecat_registry.context import RegistryContext, set_context

from tracecat import config
from tracecat.contexts import (
    ctx_interaction,
    ctx_logger,
    ctx_role,
    ctx_run,
    ctx_session_id,
)
from tracecat.executor.action_runner import get_action_runner
from tracecat.executor.backends.base import ExecutorBackend
from tracecat.executor.schemas import (
    ExecutorActionErrorInfo,
    ExecutorResult,
    ExecutorResultFailure,
    ExecutorResultSuccess,
    ResolvedContext,
)
from tracecat.executor.service import run_single_action
from tracecat.expressions.common import ExprContext
from tracecat.logger import logger
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.secrets import secrets_manager

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
        resolved_context: ResolvedContext,
        timeout: float = 300.0,
    ) -> ExecutorResult:
        """Execute action directly in-process using pre-resolved context.

        Uses the same resolution flow as other backends - secrets and args
        are pre-resolved by invoke_once before calling execute.
        """

        action_name = resolved_context.action_impl.action_name or input.task.action
        logger.debug(
            "Executing action directly (no sandbox)",
            action=action_name,
            task_ref=input.task.ref,
        )

        # Get tarball paths from the action runner's cache
        tarball_paths: list[str] = []
        runner = get_action_runner()
        cache_dir = runner.cache_dir
        if cache_dir.exists():
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
                    self._execute_with_context(input, role, resolved_context),
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

    async def _execute_with_context(
        self,
        input: RunActionInput,
        role: Role,
        resolved_context: ResolvedContext,
    ) -> Any:
        """Execute action using pre-resolved context.

        Uses resolved_context.action_impl to determine what to execute.
        This allows the backend to execute any action, not just the one
        specified in input.task.action (important for template step execution).
        """
        action_impl = resolved_context.action_impl

        # Templates should be orchestrated at the service layer
        # This backend should only receive UDF invocations
        if action_impl.type == "template":
            raise NotImplementedError(
                "Template actions must be orchestrated at the service layer. "
                "DirectBackend should only receive UDF invocations."
            )

        # Prefer registry action name for logging when available.
        action_name = action_impl.action_name or (
            f"{action_impl.module}.{action_impl.name}"
            if action_impl.module
            else action_impl.name or "unknown"
        )

        # Set context variables (matches untrusted_runner.py)
        ctx_role.set(role)
        ctx_run.set(input.run_context)
        ctx_session_id.set(input.session_id)
        # Always set interaction context (even if None) to prevent stale context leakage
        ctx_interaction.set(input.interaction_context)

        log = ctx_logger.get(logger.bind(ref=input.task.ref))

        # Set up registry context for SDK access within UDFs
        registry_ctx = RegistryContext(
            workspace_id=resolved_context.workspace_id,
            workflow_id=resolved_context.workflow_id,
            run_id=resolved_context.run_id,
            environment=input.run_context.environment,
            api_url=config.TRACECAT__API_URL,
            token=resolved_context.executor_token,
        )
        set_context(registry_ctx)

        # Load action implementation using module/name from resolved_context
        # This allows executing the correct action for template steps
        action = await self._load_action_from_impl(action_impl)

        log.info(
            "Run action",
            task_ref=input.task.ref,
            action_name=action_name,
        )

        # Build execution context with pre-resolved secrets and variables
        context = input.exec_context.copy()
        context[ExprContext.SECRETS] = resolved_context.secrets
        context[ExprContext.VARS] = resolved_context.variables

        # Flatten secrets for env sandbox
        flattened_secrets = secrets_manager.flatten_secrets(resolved_context.secrets)

        # Initialize registry secrets context for SDK mode
        secrets_token = registry_secrets.set_context(flattened_secrets)

        try:
            # Execute with secrets in environment
            args = resolved_context.evaluated_args or {}
            with secrets_manager.env_sandbox(flattened_secrets):
                result = await run_single_action(
                    action=action,
                    args=args,
                    context=context,
                )

            log.trace("Result", result=result)
            return result
        finally:
            # Reset secrets context to prevent leakage
            registry_secrets.reset_context(secrets_token)

    async def _load_action_from_impl(self, action_impl) -> Any:
        """Load the action implementation from action_impl metadata.

        Uses the module and name from action_impl to load the correct
        action, regardless of what's in input.task.action.
        """
        async with RegistryActionsService.with_session() as service:
            # Fast path: load by registry action name when provided.
            if action_impl.action_name:
                reg_action = await service.get_action(action_impl.action_name)
                return service.get_bound(reg_action, mode="execution")

            # Fallback: resolve via implementation metadata (slower JSONB scan).
            if not action_impl.module or not action_impl.name:
                raise ValueError(
                    "UDF action missing module or name: "
                    f"module={action_impl.module}, name={action_impl.name}"
                )
            reg_action = await service.get_action_by_impl(
                module=action_impl.module,
                name=action_impl.name,
            )
            return service.get_bound(reg_action, mode="execution")
