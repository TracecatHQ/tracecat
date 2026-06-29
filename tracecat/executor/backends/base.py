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

import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

from tracecat import config
from tracecat.dsl.enums import PlatformAction
from tracecat.executor.action_gateway.config import action_gateway_socket_path
from tracecat.executor.action_runner import get_action_runner
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
            return await self._execute_run_python(input, role, resolved_context)

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
        input: RunActionInput,
        role: Role,
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

        env_vars = self._build_run_python_env_vars(
            input,
            resolved_context,
            user_env_vars=args.get("env_vars"),
        )
        registry_paths = await self._resolve_run_python_registry_paths(input, role)
        if isinstance(registry_paths, ExecutorActionErrorInfo):
            return ExecutorResultFailure(error=registry_paths)

        service = SandboxService()
        try:
            result = await service.run_python(
                script=script,
                inputs=args.get("inputs"),
                dependencies=args.get("dependencies"),
                timeout_seconds=args.get("timeout_seconds", 300),
                allow_network=args.get("allow_network", False),
                env_vars=env_vars,
                python_path_dirs=registry_paths,
                workspace_id=resolved_context.workspace_id,
                action_gateway_socket=action_gateway_socket_path(),
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

    async def _resolve_run_python_registry_paths(
        self,
        input: RunActionInput,
        role: Role,
    ) -> list[Path] | ExecutorActionErrorInfo:
        """Resolve registry artifact paths for run_python SDK imports."""
        artifact_uris = await self._get_artifact_uris(input, role)
        if not artifact_uris:
            if config.TRACECAT__LOCAL_REPOSITORY_ENABLED:
                if local_registry_paths := self._get_run_python_local_registry_paths():
                    return local_registry_paths
                message = (
                    "No local registry paths available for run_python execution. "
                    "Check TRACECAT__BUILTIN_REGISTRY_SOURCE_PATH, "
                    "TRACECAT__LOCAL_REPOSITORY_CONTAINER_PATH, and PYTHONUSERBASE."
                )
            else:
                message = (
                    "No registry artifacts available for run_python execution. "
                    "Check that the registry is synced and the registry_lock is valid."
                )

            return ExecutorActionErrorInfo(
                action_name=PlatformAction.RUN_PYTHON,
                type="RegistryError",
                message=message,
                filename="base.py",
                function="_execute_run_python",
            )
        return await get_action_runner().resolve_registry_paths(artifact_uris)

    def _get_run_python_local_registry_paths(self) -> list[Path]:
        """Return local registry import roots for run_python local-repository mode."""
        repo_root = Path(__file__).resolve().parents[3]
        builtin_source_path = Path(config.TRACECAT__BUILTIN_REGISTRY_SOURCE_PATH)
        candidates = [builtin_source_path]
        if not builtin_source_path.exists():
            candidates.append(repo_root / "packages" / "tracecat-registry")
        custom_registry_target = Path(
            os.getenv("PYTHONUSERBASE") or Path.home().joinpath(".local")
        )
        candidates.extend(
            [
                Path(config.TRACECAT__LOCAL_REPOSITORY_CONTAINER_PATH),
                custom_registry_target,
            ]
        )

        paths: list[Path] = []
        seen: set[Path] = set()
        for path in candidates:
            if not path.exists():
                continue
            resolved_path = path.resolve()
            if resolved_path in seen:
                continue
            seen.add(resolved_path)
            paths.append(path)
        return paths

    @abstractmethod
    async def _get_artifact_uris(
        self,
        input: RunActionInput,
        role: Role,
    ) -> list[str]:
        """Get registry artifact URIs for this backend."""
        ...

    def _build_run_python_env_vars(
        self,
        input: RunActionInput,
        resolved_context: ResolvedContext,
        *,
        user_env_vars: dict[str, str] | None,
    ) -> dict[str, str]:
        """Build run_python environment with Tracecat SDK context always enabled."""

        env_vars = dict(user_env_vars or {})
        env_vars.update(
            {
                "TRACECAT__API_URL": config.TRACECAT__API_URL,
                "TRACECAT__WORKSPACE_ID": resolved_context.workspace_id,
                "TRACECAT__WORKFLOW_ID": resolved_context.workflow_id,
                "TRACECAT__RUN_ID": resolved_context.run_id,
                "TRACECAT__WF_EXEC_ID": str(input.run_context.wf_exec_id),
                "TRACECAT__ENVIRONMENT": input.run_context.environment,
                "TRACECAT__EXECUTOR_TOKEN": resolved_context.executor_token,
            }
        )
        return env_vars

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
