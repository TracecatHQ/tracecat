"""Executor backend abstraction layer.

This module defines the abstract base class for executor backends,
enabling pluggable execution strategies for different deployment scenarios.

Available backends:
- pool: Warm nsjail workers for single-tenant, high throughput
- ephemeral: Cold nsjail subprocess per action for multitenant workloads
- direct: Direct subprocess execution without warm workers
- test: In-process execution for tests only
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from tracecat.dsl.enums import PlatformAction
from tracecat.executor.schemas import (
    ExecutorActionErrorInfo,
    ExecutorResult,
    ExecutorResultFailure,
    ExecutorResultSuccess,
)
from tracecat.sandbox import (
    PackageInstallError,
    SandboxExecutionError,
    SandboxService,
    SandboxTimeoutError,
    SandboxValidationError,
    validate_run_python_script,
)

if TYPE_CHECKING:
    from tracecat.auth.types import Role
    from tracecat.dsl.schemas import RunActionInput
    from tracecat.executor.schemas import ResolvedContext


class ExecutorBackend(ABC):
    """Abstract base class for executor backends.

    Backends implement different execution strategies with varying
    trade-offs between isolation, latency, and resource usage.

    All backends receive a pre-resolved ResolvedContext containing:
    - secrets: Pre-resolved secrets
    - variables: Pre-resolved workspace variables
    - action_impl: Action implementation metadata
    - evaluated_args: Pre-evaluated action arguments
    - Execution context (workspace_id, workflow_id, run_id, executor_token)
    """

    async def execute(
        self,
        input: RunActionInput,
        role: Role,
        resolved_context: ResolvedContext,
        timeout: float = 300.0,
    ) -> ExecutorResult:
        """Execute an action and return result.

        This is the public entry point. It handles routing for platform
        actions that need special handling, then delegates to _execute()
        for normal UDF execution.

        Args:
            input: The RunActionInput containing task definition and context
            role: The Role for authorization
            resolved_context: Pre-resolved secrets, variables, action impl, and args
            timeout: Execution timeout in seconds

        Returns:
            ExecutorResultSuccess on success, ExecutorResultFailure on failure.
        """
        action_name = resolved_context.action_impl.action_name

        # Platform actions with special execution requirements
        if action_name == PlatformAction.RUN_PYTHON:
            return await self._execute_run_python(resolved_context)

        # Normal UDF execution via backend-specific implementation
        return await self._execute(input, role, resolved_context, timeout)

    @abstractmethod
    async def _execute(
        self,
        input: RunActionInput,
        role: Role,
        resolved_context: ResolvedContext,
        timeout: float = 300.0,
    ) -> ExecutorResult:
        """Backend-specific execution implementation.

        Subclasses implement this for their specific execution strategy.
        """
        ...

    async def _execute_run_python(
        self,
        resolved_context: ResolvedContext,
    ) -> ExecutorResult:
        """Execute run_python action using host sandbox.

        This executes directly on the host rather than delegating to
        backend workers, because run_python needs its own nsjail sandbox.
        """
        args = resolved_context.evaluated_args
        script = args.get("script", "")

        # Validate script structure
        is_valid, error_message = validate_run_python_script(script)
        if not is_valid:
            error_info = ExecutorActionErrorInfo(
                action_name="core.script.run_python",
                type="ValidationError",
                message=error_message or "Script validation failed",
                filename="base.py",
                function="_execute_run_python",
            )
            return ExecutorResultFailure(error=error_info)

        service = SandboxService()

        try:
            result = await service.run_python(
                script=script,
                inputs=args.get("inputs"),
                dependencies=args.get("dependencies"),
                timeout_seconds=args.get("timeout_seconds", 300),
                allow_network=args.get("allow_network", False),
                env_vars=args.get("env_vars"),
                workspace_id=resolved_context.workspace_id,
            )
            return ExecutorResultSuccess(result=result)
        except (
            SandboxTimeoutError,
            SandboxValidationError,
            SandboxExecutionError,
            PackageInstallError,
        ) as e:
            error_info = ExecutorActionErrorInfo(
                action_name="core.script.run_python",
                type=type(e).__name__,
                message=str(e),
                filename="base.py",
                function="_execute_run_python",
            )
            return ExecutorResultFailure(error=error_info)

    async def start(self) -> None:  # noqa: B027
        """Initialize the backend.

        Called once at worker startup. Override to perform setup
        like creating worker pools or establishing connections.
        """

    async def shutdown(self) -> None:  # noqa: B027
        """Cleanup backend resources.

        Called at worker shutdown. Override to release resources
        like terminating worker processes or closing connections.
        """
