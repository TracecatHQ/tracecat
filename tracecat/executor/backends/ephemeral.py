"""Ephemeral nsjail subprocess executor backend (untrusted mode).

This backend creates a fresh nsjail sandbox for each action execution,
providing maximum isolation for multitenant workloads. Each action runs
in complete isolation with no shared state.

All execution is untrusted - DB credentials are never passed to sandboxes.
Secrets and variables are pre-resolved on the host.

Best for multitenant deployments with untrusted workloads.
"""

from __future__ import annotations

from tracecat import config
from tracecat.auth.types import Role
from tracecat.dsl.schemas import RunActionInput
from tracecat.executor.action_runner import get_action_runner
from tracecat.executor.backends.base import ExecutorBackend
from tracecat.executor.schemas import (
    ExecutorActionErrorInfo,
    ExecutorResult,
    ExecutorResultFailure,
    ExecutorResultSuccess,
    ResolvedContext,
)
from tracecat.executor.service import (
    get_registry_artifacts_cached,
    get_registry_artifacts_for_lock,
)
from tracecat.logger import logger


class EphemeralBackend(ExecutorBackend):
    """Ephemeral nsjail subprocess backend (untrusted mode).

    Creates a fresh nsjail sandbox for each action execution, providing:

    - Complete isolation between actions (no shared workers)
    - Full OS-level sandboxing (namespaces, seccomp, resource limits)
    - Suitable for untrusted multitenant workloads
    - No DB credentials passed to sandbox (untrusted mode)

    Trade-offs:
    - Higher latency (~4000ms cold start per action)
    - Higher resource usage (new process per action)
    - Lower throughput than pooled execution

    Each action runs in its own nsjail sandbox with:
    - PID namespace isolation
    - Mount namespace isolation
    - User namespace isolation
    - IPC namespace isolation
    - UTS namespace isolation
    - Cgroup namespace isolation
    - Seccomp syscall filtering
    - Resource limits (CPU, memory, file size)
    """

    async def execute(
        self,
        input: RunActionInput,
        role: Role,
        resolved_context: ResolvedContext,
        timeout: float = 300.0,
    ) -> ExecutorResult:
        """Execute action in an ephemeral nsjail sandbox (untrusted mode)."""
        action_name = input.task.action

        logger.debug(
            "Executing action in ephemeral sandbox",
            action=action_name,
            task_ref=input.task.ref,
        )

        # Get tarball URI for registry environment
        tarball_uri = await self._get_tarball_uri(input, role)

        # Execute using ActionRunner with forced sandbox mode
        runner = get_action_runner()
        result = await runner.execute_action(
            input=input,
            role=role,
            resolved_context=resolved_context,
            tarball_uri=tarball_uri,
            timeout=timeout,
            force_sandbox=True,  # Always use nsjail for ephemeral backend
        )

        # Convert to standard response format
        if isinstance(result, ExecutorActionErrorInfo):
            return ExecutorResultFailure(error=result)
        return ExecutorResultSuccess(result=result)

    async def _get_tarball_uri(self, input: RunActionInput, role: Role) -> str | None:
        """Get the tarball URI for the registry environment."""
        if config.TRACECAT__LOCAL_REPOSITORY_ENABLED:
            return None

        try:
            if input.registry_lock:
                artifacts = await get_registry_artifacts_for_lock(input.registry_lock)
            else:
                artifacts = await get_registry_artifacts_cached(role)

            # Return first tarball URI if available
            for artifact in artifacts:
                if artifact.tarball_uri:
                    return artifact.tarball_uri
        except Exception as e:
            logger.warning(
                "Failed to load registry artifacts for ephemeral execution",
                error=str(e),
            )

        return None
