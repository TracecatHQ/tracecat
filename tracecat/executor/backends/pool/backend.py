"""Worker pool executor backend.

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

from tracecat.executor.backends.base import ExecutorBackend
from tracecat.executor.backends.pool.pool import (
    get_worker_pool,
    shutdown_worker_pool,
)
from tracecat.logger import logger

if TYPE_CHECKING:
    from tracecat.auth.types import Role
    from tracecat.dsl.schemas import RunActionInput
    from tracecat.executor.schemas import ExecutorResult, ResolvedContext


class PoolBackend(ExecutorBackend):
    """Warm nsjail worker pool backend.

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

        pool = await get_worker_pool()
        return await pool.execute(
            input=input,
            role=role,
            resolved_context=resolved_context,
            timeout=timeout,
        )

    async def start(self) -> None:
        """Initialize the worker pool."""
        logger.info("Starting pool backend")
        await get_worker_pool()

    async def shutdown(self) -> None:
        """Shutdown the worker pool."""
        logger.info("Shutting down pool backend")
        await shutdown_worker_pool()
