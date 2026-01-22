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
    RegistryArtifactsContext,
    get_registry_artifacts_for_lock,
)
from tracecat.logger import logger
from tracecat.registry.constants import DEFAULT_REGISTRY_ORIGIN


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

    async def _execute(
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

        # Get ALL tarball URIs for registry environment (deterministic ordering)
        tarball_uris = await self._get_tarball_uris(input, role)

        # Error out early if no tarballs resolved (unless local repository is enabled)
        if not tarball_uris and not config.TRACECAT__LOCAL_REPOSITORY_ENABLED:
            logger.error(
                "No registry tarballs resolved - cannot execute action",
                action=action_name,
                action_origin=resolved_context.action_impl.origin,
            )
            return ExecutorResultFailure(
                error=ExecutorActionErrorInfo(
                    type="RegistryError",
                    message="No registry tarballs available for execution. "
                    "Check that the registry is synced and the registry_lock is valid.",
                    action_name=action_name,
                    filename="<ephemeral>",
                    function="execute",
                )
            )

        if tarball_uris:
            logger.debug("Mounting registry tarballs", count=len(tarball_uris))
        else:
            logger.debug("Using local repository mode, no tarballs mounted")

        # Execute using ActionRunner with forced sandbox mode
        runner = get_action_runner()
        result = await runner.execute_action(
            input=input,
            role=role,
            resolved_context=resolved_context,
            tarball_uris=tarball_uris,
            timeout=timeout,
            force_sandbox=True,  # Always use nsjail for ephemeral backend
        )

        # Convert to standard response format
        if isinstance(result, ExecutorActionErrorInfo):
            return ExecutorResultFailure(error=result)
        return ExecutorResultSuccess(result=result)

    async def _get_tarball_uris(
        self,
        input: RunActionInput,
        role: Role,
    ) -> list[str]:
        """Get tarball URIs for registry environment (deterministic ordering).

        Args:
            input: The RunActionInput containing task and execution context

        Returns:
            List of tarball URIs in deterministic order (tracecat_registry first,
            then lexicographically by origin).
        """
        if config.TRACECAT__LOCAL_REPOSITORY_ENABLED:
            return []

        try:
            artifacts = await get_registry_artifacts_for_lock(
                input.registry_lock.origins, role.organization_id
            )
            return self._sort_tarball_uris(artifacts)
        except Exception as e:
            logger.warning(
                "Failed to load registry artifacts for ephemeral execution",
                error=str(e),
            )
            return []

    def _sort_tarball_uris(
        self, artifacts: list[RegistryArtifactsContext]
    ) -> list[str]:
        """Sort tarballs: tracecat_registry first, then lexicographically by origin."""
        builtin_uris: list[str] = []
        other_uris: list[tuple[str, str]] = []  # (origin, uri)

        for artifact in artifacts:
            if not artifact.tarball_uri:
                continue
            if artifact.origin == DEFAULT_REGISTRY_ORIGIN:
                builtin_uris.append(artifact.tarball_uri)
            else:
                other_uris.append((artifact.origin, artifact.tarball_uri))

        # Sort non-builtin by origin lexicographically
        other_uris.sort(key=lambda x: x[0])

        return builtin_uris + [uri for _, uri in other_uris]
