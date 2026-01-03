"""Executor backend abstraction layer.

This module defines the abstract base class for executor backends,
enabling pluggable execution strategies for different deployment scenarios.

Available backends:
- sandboxed_pool: Warm nsjail workers for single-tenant, high throughput
- ephemeral: Cold nsjail subprocess per action for multitenant workloads
- direct: In-process execution for development only
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tracecat.auth.types import Role
    from tracecat.dsl.schemas import RunActionInput
    from tracecat.executor.schemas import ExecutorResult


class ExecutorBackend(ABC):
    """Abstract base class for executor backends.

    Backends implement different execution strategies with varying
    trade-offs between isolation, latency, and resource usage.
    """

    @abstractmethod
    async def execute(
        self,
        input: RunActionInput,
        role: Role,
        timeout: float = 300.0,
    ) -> ExecutorResult:
        """Execute an action and return result.

        Args:
            input: The RunActionInput containing task definition and context
            role: The Role for authorization
            timeout: Execution timeout in seconds

        Returns:
            ExecutorResultSuccess on success, ExecutorResultFailure on failure.
        """
        ...

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
