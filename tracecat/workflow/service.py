from __future__ import annotations

from collections.abc import AsyncGenerator

from temporalio.api.enums.v1 import EventType
from temporalio.client import (
    Client,
    WorkflowExecution,
    WorkflowExecutionDescription,
    WorkflowExecutionStatus,
    WorkflowHandle,
    WorkflowHistory,
    WorkflowHistoryEventFilterType,
)

from tracecat import identifiers
from tracecat.contexts import ctx_role
from tracecat.dsl.client import get_temporal_client
from tracecat.logging import logger
from tracecat.types.auth import Role
from tracecat.workflow.models import EventHistoryResponse, EventHistoryType


class WorkflowExecutionsService:
    """Workflow executions service."""

    def __init__(self, client: Client, role: Role | None = None):
        self.role = role or ctx_role.get()
        self._client = client
        self.logger = logger.bind(service="workflow_executions")

    @staticmethod
    async def connect() -> WorkflowExecutionsService:
        """Initialize and connect to the service."""
        client = await get_temporal_client()
        return WorkflowExecutionsService(client=client)

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

    async def list_workflow_execution_event_history(
        self,
        wf_exec_id: identifiers.WorkflowExecutionID,
        event_filter_type: WorkflowHistoryEventFilterType = WorkflowHistoryEventFilterType.ALL_EVENT,
        **kwargs,
    ) -> list[EventHistoryResponse]:
        """List the event history of a workflow execution."""

        history = await self.handle(wf_exec_id).fetch_history(
            event_filter_type=event_filter_type, **kwargs
        )
        events = []
        for event in history.events:
            match event.event_type:
                case EventType.EVENT_TYPE_WORKFLOW_EXECUTION_STARTED:
                    events.append(
                        EventHistoryResponse(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(),
                            event_type=EventHistoryType.WORKFLOW_EXECUTION_STARTED,
                            task_id=event.task_id,
                            details=event.workflow_execution_started_event_attributes,
                        )
                    )
                case EventType.EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED:
                    events.append(
                        EventHistoryResponse(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(),
                            event_type=EventHistoryType.WORKFLOW_EXECUTION_COMPLETED,
                            task_id=event.task_id,
                            details=event.workflow_execution_completed_event_attributes,
                        )
                    )
                case EventType.EVENT_TYPE_WORKFLOW_EXECUTION_FAILED:
                    events.append(
                        EventHistoryResponse(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(),
                            event_type=EventHistoryType.WORKFLOW_EXECUTION_FAILED,
                            task_id=event.task_id,
                            details=event.workflow_execution_failed_event_attributes,
                        )
                    )
                case EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED:
                    events.append(
                        EventHistoryResponse(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(),
                            event_type=EventHistoryType.ACTIVITY_TASK_SCHEDULED,
                            task_id=event.task_id,
                            details=event.activity_task_scheduled_event_attributes,
                        )
                    )
                case EventType.EVENT_TYPE_ACTIVITY_TASK_STARTED:
                    events.append(
                        EventHistoryResponse(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(),
                            event_type=EventHistoryType.ACTIVITY_TASK_STARTED,
                            task_id=event.task_id,
                            details=event.activity_task_started_event_attributes,
                        )
                    )
                case EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED:
                    events.append(
                        EventHistoryResponse(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(),
                            event_type=EventHistoryType.ACTIVITY_TASK_COMPLETED,
                            task_id=event.task_id,
                            details=event.activity_task_completed_event_attributes,
                        )
                    )
                case EventType.EVENT_TYPE_ACTIVITY_TASK_FAILED:
                    events.append(
                        EventHistoryResponse(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(),
                            event_type=EventHistoryType.ACTIVITY_TASK_FAILED,
                            task_id=event.task_id,
                            details=event.activity_task_failed_event_attributes,
                        )
                    )
                case _:
                    continue
        return events

    async def iter_list_workflow_execution_event_history(
        self,
        wf_exec_id: identifiers.WorkflowExecutionID,
        event_filter_type: WorkflowHistoryEventFilterType = WorkflowHistoryEventFilterType.ALL_EVENT,
        **kwargs,
    ) -> AsyncGenerator[WorkflowHistory]:
        """List the event history of a workflow execution."""

        handle = self.handle(wf_exec_id)
        async for event in handle.fetch_history_events(
            page_size=10, event_filter_type=event_filter_type, **kwargs
        ):
            yield event
