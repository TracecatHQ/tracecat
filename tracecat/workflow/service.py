from temporalio.client import Client as TemporalClient
from temporalio.client import (
    WorkflowExecution,
    WorkflowExecutionDescription,
    WorkflowExecutionStatus,
    WorkflowHandle,
)

from tracecat import identifiers
from tracecat.contexts import ctx_role
from tracecat.logging import logger
from tracecat.types.auth import Role


class WorkflowExecutionsService:
    """Workflow executions service."""

    def __init__(self, client: TemporalClient, role: Role | None = None):
        self.role = role or ctx_role.get()
        self._client = client
        self.logger = logger.bind(service="workflow_executions")

    def handle(self, wf_exec_id: identifiers.WorkflowExecutionID) -> WorkflowHandle:
        return self._client.get_workflow_handle(wf_exec_id)

    async def query_executions(
        self, query: str | None = None, **kwargs
    ) -> list[WorkflowExecutionDescription]:
        # Invoke with async for
        return [
            wf_exec
            async for wf_exec in self._client.list_workflows(query=query, **kwargs)
        ]

    async def get_execution(
        self, wf_exec_id: identifiers.WorkflowExecutionID
    ) -> WorkflowExecutionDescription:
        return await self.handle(wf_exec_id).describe()

    async def get_execution_status(
        self, wf_exec_id: identifiers.WorkflowExecutionID
    ) -> WorkflowExecutionStatus | None:
        """Get the status of a workflow execution."""

        description = await self.handle(wf_exec_id).describe()
        return description.status if description else None

    async def list_executions(self) -> list[WorkflowExecution]:
        """List all workflow executions."""

        return await self.query_executions()

    async def list_executions_by_workflow_id(
        self, wf_id: identifiers.WorkflowID
    ) -> list[WorkflowExecution]:
        """List all workflow executions by workflow ID."""

        query = f"WorkflowId STARTS_WITH {wf_id!r}"
        return await self.query_executions(query=query)

    async def get_latest_execution_by_workflow_id(
        self, wf_id: identifiers.WorkflowID
    ) -> WorkflowExecution:
        """Get the latest workflow execution by workflow ID."""

        executions = await self.list_executions_by_workflow_id(wf_id)
        return max(executions, key=lambda exec: exec.start_time)
