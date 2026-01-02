"""Ephemeral nsjail subprocess executor backend.

This backend creates a fresh nsjail sandbox for each action execution,
providing maximum isolation for multitenant workloads. Each action runs
in complete isolation with no shared state.

Best for multitenant deployments with untrusted workloads.

Trust Modes:
- 'trusted': Pass DB credentials to sandbox (single-tenant)
- 'untrusted': Use SDK for secrets/variables (multitenant, default for ephemeral)
"""

from __future__ import annotations

from typing import Literal

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
)
from tracecat.executor.service import (
    get_registry_artifacts_cached,
    get_registry_artifacts_for_lock,
)
from tracecat.logger import logger


class EphemeralBackend(ExecutorBackend):
    """Ephemeral nsjail subprocess backend.

    Creates a fresh nsjail sandbox for each action execution, providing:

    - Complete isolation between actions (no shared workers)
    - Full OS-level sandboxing (namespaces, seccomp, resource limits)
    - Suitable for untrusted multitenant workloads

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

    Trust modes:
    - 'trusted': Pass DB credentials to sandbox. Actions can directly
      access the database for secrets/variables. Use for single-tenant.
    - 'untrusted': Do NOT pass DB credentials. Secrets/variables are
      pre-resolved on the host. Use for multitenant with untrusted code.
    """

    @property
    def trust_mode(self) -> Literal["trusted", "untrusted"]:
        """Get the trust mode for this backend.

        Ephemeral backend always uses 'untrusted' mode to preserve isolation
        guarantees. This ensures DB credentials are never passed into the sandbox.
        """
        return "untrusted"

    async def execute(
        self,
        input: RunActionInput,
        role: Role,
        timeout: float = 300.0,
    ) -> ExecutorResult:
        """Execute action in an ephemeral nsjail sandbox."""
        action_name = input.task.action
        trust_mode = self.trust_mode

        logger.debug(
            "Executing action in ephemeral sandbox",
            action=action_name,
            task_ref=input.task.ref,
            trust_mode=trust_mode,
        )

        # Get tarball URI for registry environment
        tarball_uri = await self._get_tarball_uri(input, role)

        # Execute using ActionRunner with forced sandbox mode
        runner = get_action_runner()
        result = await runner.execute_action(
            input=input,
            role=role,
            tarball_uri=tarball_uri,
            timeout=timeout,
            force_sandbox=True,  # Always use nsjail for ephemeral backend
            trust_mode=trust_mode,
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
