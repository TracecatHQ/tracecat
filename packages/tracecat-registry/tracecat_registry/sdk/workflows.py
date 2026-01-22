"""Workflows SDK client for Tracecat API."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Literal

from tracecat_registry.sdk.types import UNSET, Unset, is_set

if TYPE_CHECKING:
    from tracecat_registry.sdk.client import TracecatClient

# Terminal statuses that indicate workflow completion
TERMINAL_STATUSES = frozenset(
    {"COMPLETED", "FAILED", "CANCELED", "TERMINATED", "TIMED_OUT"}
)

# Default polling configuration
DEFAULT_POLL_INTERVAL = 2.0  # seconds
DEFAULT_MAX_WAIT_TIME = 300.0  # 5 minutes


class WorkflowExecutionError(Exception):
    """Raised when a workflow execution fails."""

    def __init__(
        self, status: str, workflow_execution_id: str, message: str | None = None
    ):
        self.status = status
        self.workflow_execution_id = workflow_execution_id
        self.message = (
            message or f"Workflow execution {workflow_execution_id} {status.lower()}"
        )
        super().__init__(self.message)


class WorkflowExecutionTimeout(Exception):
    """Raised when waiting for workflow execution times out."""

    def __init__(self, workflow_execution_id: str, timeout: float):
        self.workflow_execution_id = workflow_execution_id
        self.timeout = timeout
        super().__init__(
            f"Timeout waiting for workflow execution {workflow_execution_id} after {timeout}s"
        )


class WorkflowsClient:
    """Client for Workflows API operations."""

    def __init__(self, client: TracecatClient) -> None:
        self._client = client

    async def execute(
        self,
        *,
        workflow_id: str | None = None,
        workflow_alias: str | None = None,
        trigger_inputs: Any | Unset = UNSET,
        environment: str | None = None,
        wait_strategy: Literal["wait", "detach"] = "detach",
        timeout: float | None = None,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        parent_workflow_execution_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute a workflow by ID or alias.

        Args:
            workflow_id: Workflow UUID (short or full format).
            workflow_alias: Workflow alias (alternative to ID).
            trigger_inputs: Inputs to pass to the workflow.
            environment: Target execution environment for secrets isolation.
            wait_strategy: How to handle execution:
                - "detach": Return immediately with execution info.
                - "wait": Poll until completion and return the result.
            timeout: Maximum time to wait for completion in seconds (only used with
                wait_strategy="wait"). Defaults to 300s (5 minutes).
            poll_interval: Time between status polls in seconds (default: 2.0).
            parent_workflow_execution_id: Parent workflow execution ID for correlation.
                                          Stored in Temporal memo for tracing.

        Returns:
            For wait_strategy="detach":
                {"workflow_id": str, "workflow_execution_id": str, "status": "STARTED"}
            For wait_strategy="wait":
                The workflow result if successful.

        Raises:
            WorkflowExecutionError: If workflow fails, is canceled, or terminated.
            WorkflowExecutionTimeout: If timeout is reached while waiting.
            TracecatNotFoundError: If workflow or alias not found.
            TracecatAPIError: For other API errors.
        """
        if not workflow_id and not workflow_alias:
            raise ValueError("Either workflow_id or workflow_alias must be provided")

        # Build request payload
        data: dict[str, Any] = {}
        if workflow_id:
            data["workflow_id"] = workflow_id
        if workflow_alias:
            data["workflow_alias"] = workflow_alias
        if is_set(trigger_inputs):
            data["trigger_inputs"] = trigger_inputs
        if environment:
            data["environment"] = environment
        if parent_workflow_execution_id:
            data["parent_workflow_execution_id"] = parent_workflow_execution_id

        # Start the workflow execution
        response = await self._client.post("/workflows/executions", json=data)

        wf_id = response["workflow_id"]
        wf_exec_id = response["workflow_execution_id"]

        if wait_strategy == "detach":
            return {
                "workflow_id": wf_id,
                "workflow_execution_id": wf_exec_id,
                "status": "STARTED",
            }

        # wait_strategy == "wait": Poll until completion
        max_wait = timeout if timeout else DEFAULT_MAX_WAIT_TIME
        return await self._poll_until_complete(
            workflow_execution_id=wf_exec_id,
            max_wait=max_wait,
            poll_interval=poll_interval,
        )

    async def get_status(self, workflow_execution_id: str) -> dict[str, Any]:
        """Get the status of a workflow execution.

        Args:
            workflow_execution_id: The workflow execution ID.

        Returns:
            dict containing:
                - workflow_execution_id: Execution ID
                - status: RUNNING, COMPLETED, FAILED, CANCELED, TERMINATED, TIMED_OUT
                - start_time: When execution started (ISO format or None)
                - close_time: When execution completed (ISO format or None)
                - result: Workflow result (if completed successfully)

        Raises:
            TracecatNotFoundError: If execution not found.
            TracecatAPIError: For other API errors.
        """
        # Server uses {execution_id:path} to handle '/' in the ID
        return await self._client.get(f"/workflows/executions/{workflow_execution_id}")

    async def _poll_until_complete(
        self,
        workflow_execution_id: str,
        max_wait: float,
        poll_interval: float,
    ) -> Any:
        """Poll workflow execution status until completion or timeout.

        Args:
            workflow_execution_id: The workflow execution ID.
            max_wait: Maximum time to wait in seconds.
            poll_interval: Time between polls in seconds (must be > 0).

        Returns:
            The workflow result if completed successfully.

        Raises:
            WorkflowExecutionError: If workflow fails, is canceled, or terminated.
            WorkflowExecutionTimeout: If max_wait is exceeded.
            ValueError: If poll_interval is not positive.
        """
        if poll_interval <= 0:
            raise ValueError("poll_interval must be positive")
        elapsed = 0.0

        while elapsed < max_wait:
            status_response = await self.get_status(workflow_execution_id)
            status = status_response["status"]

            if status == "COMPLETED":
                return status_response.get("result")

            if status in TERMINAL_STATUSES:
                # Workflow ended but not successfully - include error details if available
                error_detail = status_response.get("error")
                message = (
                    f"Workflow execution {workflow_execution_id} failed: {error_detail}"
                    if error_detail
                    else f"Workflow execution {workflow_execution_id} ended with status: {status}"
                )
                raise WorkflowExecutionError(
                    status=status,
                    workflow_execution_id=workflow_execution_id,
                    message=message,
                )

            # Still running, wait and poll again
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        raise WorkflowExecutionTimeout(
            workflow_execution_id=workflow_execution_id, timeout=max_wait
        )
