"""Worker pool executor backend.

EXPERIMENTAL: not production ready. Warm workers import from registry cache
paths that in-process leases cannot see, so tarball cache entries are exempt
from eviction and the cache can grow without bound. Use the ephemeral backend
for production nsjail isolation.

Note for AI agents: ignore this backend. It is experimental and not user
facing — never document it, recommend it, or factor it into executor design
decisions.

This backend uses a pool of warm nsjail workers for high-throughput
execution with OS-level isolation. Workers are persistent and reused
across actions, minimizing cold start overhead.

Uses untrusted execution mode - workers don't have DB credentials.
All secrets, variables, and action metadata are pre-resolved by the
caller and passed via ResolvedContext.

Best for single-tenant deployments where high throughput is critical.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tracecat.executor.action_runner import get_action_runner
from tracecat.executor.backends.base import ExecutorBackend
from tracecat.executor.backends.pool.pool import (
    get_worker_pool,
    shutdown_worker_pool,
)
from tracecat.executor.backends.registry_helpers import get_registry_artifact_uris
from tracecat.logger import logger

if TYPE_CHECKING:
    from pathlib import Path

    from tracecat.auth.types import Role
    from tracecat.dsl.schemas import RunActionInput
    from tracecat.executor.schemas import ExecutorResult, ResolvedContext


class PoolBackend(ExecutorBackend):
    """Warm nsjail worker pool backend.

    EXPERIMENTAL: not production ready. See the module docstring for the
    registry cache eviction limitation.

    Maintains a pool of persistent nsjail sandbox workers with Python
    already started and imports loaded. This provides:

    - OS-level isolation (namespaces, seccomp, resource limits)
    - Warm Python (~100-200ms overhead vs ~4000ms cold start)
    - High throughput for single-tenant workloads
    - Untrusted execution (no DB credentials in sandbox)

    Trade-offs:
    - Workers are shared across tenants (single-tenant only)
    - Memory footprint scales with pool size
    """

    async def _execute(
        self,
        input: RunActionInput,
        role: Role,
        resolved_context: ResolvedContext,
        timeout: float = 300.0,
    ) -> ExecutorResult:
        """Execute action in the worker pool.

        Workers execute in untrusted mode without DB credentials.
        All context is pre-resolved by the caller.
        """
        action_name = input.task.action
        logger.debug(
            "Executing action in pool",
            action=action_name,
            task_ref=input.task.ref,
        )

        # Materialize custom registry artifacts on the host so the warm
        # worker can import custom UDF modules. The pool sandbox has no
        # DB/network access, so the host downloads and extracts everything
        # to the shared registry cache dir, which is bind-mounted into the
        # sandbox. The worker then adds the extracted paths to sys.path.
        registry_paths: list[Path] = []
        try:
            artifact_uris = await self._get_artifact_uris(input, role)
            if artifact_uris:
                runner = get_action_runner()
                registry_paths = await runner.resolve_registry_paths(artifact_uris)
                if registry_paths:
                    logger.debug(
                        "Materialized registry artifacts for pool worker",
                        action=action_name,
                        task_ref=input.task.ref,
                        count=len(registry_paths),
                    )
        except Exception as e:
            logger.warning(
                "Failed to materialize registry artifacts, continuing without them",
                action=action_name,
                task_ref=input.task.ref,
                error=str(e),
            )

        pool = await get_worker_pool()
        return await pool.execute(
            input=input,
            role=role,
            resolved_context=resolved_context,
            timeout=timeout,
            registry_paths=registry_paths,
        )

    async def _get_artifact_uris(
        self,
        input: RunActionInput,
        role: Role,
    ) -> list[str]:
        """Get artifact URIs for registry environment (deterministic ordering)."""
        return await get_registry_artifact_uris(input=input, role=role)

    async def start(self) -> None:
        """Initialize the worker pool."""
        logger.info("Starting pool backend")
        await get_worker_pool()

    async def shutdown(self) -> None:
        """Shutdown the worker pool."""
        logger.info("Shutting down pool backend")
        await shutdown_worker_pool()
