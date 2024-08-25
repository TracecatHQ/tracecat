from __future__ import annotations

import asyncio
import datetime
import json
import os
from collections.abc import AsyncGenerator, Awaitable
from typing import Any

import orjson
from temporalio.api.enums.v1 import EventType
from temporalio.client import (
    Client,
    WorkflowExecution,
    WorkflowExecutionDescription,
    WorkflowExecutionStatus,
    WorkflowFailureError,
    WorkflowHandle,
    WorkflowHistory,
    WorkflowHistoryEventFilterType,
)

from tracecat import config, identifiers
from tracecat.contexts import ctx_role
from tracecat.dsl.client import get_temporal_client
from tracecat.dsl.common import DSLInput, DSLRunArgs
from tracecat.dsl.validation import validate_trigger_inputs
from tracecat.dsl.workflow import DSLWorkflow, retry_policies
from tracecat.logging import logger
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatValidationError
from tracecat.workflow.executions.models import (
    CreateWorkflowExecutionResponse,
    DispatchWorkflowResult,
    EventFailure,
    EventGroup,
    EventHistoryResponse,
    EventHistoryType,
)


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
        event_group_names: dict[int, EventGroup | None] = {}
        events = []
        for event in history.events:
            match event.event_type:
                case EventType.EVENT_TYPE_START_CHILD_WORKFLOW_EXECUTION_INITIATED:
                    group = EventGroup.from_initiated_child_workflow(event)
                    event_group_names[event.event_id] = group
                    events.append(
                        EventHistoryResponse(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(datetime.UTC),
                            event_type=EventHistoryType.START_CHILD_WORKFLOW_EXECUTION_INITIATED,
                            event_group=group,
                            task_id=event.task_id,
                            role=group.action_input.role,
                            input=group.action_input,
                        )
                    )
                case EventType.EVENT_TYPE_CHILD_WORKFLOW_EXECUTION_STARTED:
                    parent_event_id = event.child_workflow_execution_started_event_attributes.initiated_event_id
                    group = event_group_names.get(parent_event_id)
                    event_group_names[event.event_id] = group
                    events.append(
                        EventHistoryResponse(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(datetime.UTC),
                            event_type=EventHistoryType.CHILD_WORKFLOW_EXECUTION_STARTED,
                            event_group=group,
                            task_id=event.task_id,
                        )
                    )
                case EventType.EVENT_TYPE_CHILD_WORKFLOW_EXECUTION_COMPLETED:
                    result = orjson.loads(
                        event.child_workflow_execution_completed_event_attributes.result.payloads[
                            0
                        ].data
                    )
                    initiator_event_id = event.child_workflow_execution_completed_event_attributes.initiated_event_id
                    group = event_group_names.get(initiator_event_id)
                    events.append(
                        EventHistoryResponse(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(datetime.UTC),
                            event_type=EventHistoryType.CHILD_WORKFLOW_EXECUTION_COMPLETED,
                            event_group=group,
                            task_id=event.task_id,
                            result=result,
                        )
                    )
                case EventType.EVENT_TYPE_CHILD_WORKFLOW_EXECUTION_FAILED:
                    gparent_event_id = event.child_workflow_execution_failed_event_attributes.initiated_event_id
                    group = event_group_names.get(gparent_event_id)
                    event_group_names[event.event_id] = group
                    events.append(
                        EventHistoryResponse(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(datetime.UTC),
                            event_type=EventHistoryType.CHILD_WORKFLOW_EXECUTION_FAILED,
                            event_group=group,
                            task_id=event.task_id,
                            failure=EventFailure.from_history_event(event),
                        )
                    )

                case EventType.EVENT_TYPE_WORKFLOW_EXECUTION_STARTED:
                    run_args_data = orjson.loads(
                        event.workflow_execution_started_event_attributes.input.payloads[
                            0
                        ].data
                    )
                    dsl_run_args = DSLRunArgs(**run_args_data)
                    events.append(
                        EventHistoryResponse(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(datetime.UTC),
                            event_type=EventHistoryType.WORKFLOW_EXECUTION_STARTED,
                            task_id=event.task_id,
                            role=dsl_run_args.role,
                            input=dsl_run_args,
                        )
                    )
                case EventType.EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED:
                    result = orjson.loads(
                        event.workflow_execution_completed_event_attributes.result.payloads[
                            0
                        ].data
                    )
                    events.append(
                        EventHistoryResponse(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(datetime.UTC),
                            event_type=EventHistoryType.WORKFLOW_EXECUTION_COMPLETED,
                            task_id=event.task_id,
                            result=result,
                        )
                    )
                case EventType.EVENT_TYPE_WORKFLOW_EXECUTION_FAILED:
                    events.append(
                        EventHistoryResponse(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(datetime.UTC),
                            event_type=EventHistoryType.WORKFLOW_EXECUTION_FAILED,
                            task_id=event.task_id,
                            failure=EventFailure.from_history_event(event),
                        )
                    )
                case EventType.EVENT_TYPE_WORKFLOW_EXECUTION_TERMINATED:
                    events.append(
                        EventHistoryResponse(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(datetime.UTC),
                            event_type=EventHistoryType.WORKFLOW_EXECUTION_TERMINATED,
                            task_id=event.task_id,
                        )
                    )
                case EventType.EVENT_TYPE_WORKFLOW_EXECUTION_CANCELED:
                    events.append(
                        EventHistoryResponse(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(datetime.UTC),
                            event_type=EventHistoryType.WORKFLOW_EXECUTION_CANCELED,
                            task_id=event.task_id,
                        )
                    )
                case EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED:
                    group = EventGroup.from_scheduled_activity(event)
                    event_group_names[event.event_id] = group
                    events.append(
                        EventHistoryResponse(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(datetime.UTC),
                            event_type=EventHistoryType.ACTIVITY_TASK_SCHEDULED,
                            task_id=event.task_id,
                            event_group=group,
                        )
                    )
                case EventType.EVENT_TYPE_ACTIVITY_TASK_STARTED:
                    # The parent event here is always the scheduled event, which has the UDF name
                    parent_event_id = (
                        event.activity_task_started_event_attributes.scheduled_event_id
                    )
                    group = event_group_names.get(parent_event_id)
                    event_group_names[event.event_id] = group
                    events.append(
                        EventHistoryResponse(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(datetime.UTC),
                            event_type=EventHistoryType.ACTIVITY_TASK_STARTED,
                            task_id=event.task_id,
                            event_group=group,
                        )
                    )
                case EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED:
                    # The task completiong comes with the scheduled event ID and the started event id
                    gparent_event_id = event.activity_task_completed_event_attributes.scheduled_event_id
                    group = event_group_names.get(gparent_event_id)
                    event_group_names[event.event_id] = group
                    result = orjson.loads(
                        event.activity_task_completed_event_attributes.result.payloads[
                            0
                        ].data
                    )
                    events.append(
                        EventHistoryResponse(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(datetime.UTC),
                            event_type=EventHistoryType.ACTIVITY_TASK_COMPLETED,
                            task_id=event.task_id,
                            event_group=group,
                            result=result,
                        )
                    )
                case EventType.EVENT_TYPE_ACTIVITY_TASK_FAILED:
                    gparent_event_id = (
                        event.activity_task_failed_event_attributes.scheduled_event_id
                    )
                    group = event_group_names.get(gparent_event_id)
                    event_group_names[event.event_id] = group
                    events.append(
                        EventHistoryResponse(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(datetime.UTC),
                            event_type=EventHistoryType.ACTIVITY_TASK_FAILED,
                            task_id=event.task_id,
                            event_group=group,
                            failure=EventFailure.from_history_event(event),
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

    def create_workflow_execution_nowait(
        self,
        dsl: DSLInput,
        *,
        wf_id: identifiers.WorkflowID,
        payload: dict[str, Any] | None = None,
        enable_runtime_tests: bool = False,
    ) -> CreateWorkflowExecutionResponse:
        """Create a new workflow execution.

        Note: This method schedules the workflow execution and returns immediately.
        """
        coro = self.create_workflow_execution(
            dsl=dsl,
            wf_id=wf_id,
            payload=payload,
            enable_runtime_tests=enable_runtime_tests,
        )
        _ = asyncio.create_task(coro)
        return CreateWorkflowExecutionResponse(
            message="Workflow execution started",
            wf_id=wf_id,
            wf_exec_id=identifiers.workflow.exec_id(wf_id),
        )

    def create_workflow_execution(
        self,
        dsl: DSLInput,
        *,
        wf_id: identifiers.WorkflowID,
        payload: dict[str, Any] | None = None,
        enable_runtime_tests: bool = False,
    ) -> Awaitable[DispatchWorkflowResult]:
        """Create a new workflow execution.

        Note: This method blocks until the workflow execution completes.
        """
        validation_result = validate_trigger_inputs(dsl=dsl, payload=payload)
        if validation_result.status == "error":
            logger.error(validation_result.msg, detail=validation_result.detail)
            raise TracecatValidationError(
                validation_result.msg, detail=validation_result.detail
            )

        dsl.config.enable_runtime_tests = enable_runtime_tests
        wf_exec_id = identifiers.workflow.exec_id(wf_id)
        return self._dispatch_workflow(
            dsl=dsl,
            wf_id=wf_id,
            wf_exec_id=wf_exec_id,
            trigger_inputs=payload,
        )

    async def _dispatch_workflow(
        self,
        dsl: DSLInput,
        wf_id: identifiers.WorkflowID,
        wf_exec_id: identifiers.WorkflowExecutionID,
        trigger_inputs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> DispatchWorkflowResult:
        logger.info(
            f"Executing DSL workflow: {dsl.title}",
            role=self.role,
            wf_exec_id=wf_exec_id,
        )
        try:
            result = await self._client.execute_workflow(
                DSLWorkflow.run,
                DSLRunArgs(
                    dsl=dsl, role=self.role, wf_id=wf_id, trigger_inputs=trigger_inputs
                ),
                id=wf_exec_id,
                task_queue=config.TEMPORAL__CLUSTER_QUEUE,
                retry_policy=retry_policies["workflow:fail_fast"],
                **kwargs,
            )
        except WorkflowFailureError as e:
            self.logger.error(str(e), role=self.role, wf_exec_id=wf_exec_id, e=e)
            raise e
        except Exception as e:
            self.logger.exception(
                "Unexpected workflow error", role=self.role, wf_exec_id=wf_exec_id, e=e
            )
            raise e
        # Write result to file for debugging
        if os.getenv("DUMP_TRACECAT_RESULT", "0") in ("1", "true"):
            path = config.TRACECAT__EXECUTIONS_DIR / f"{wf_exec_id}.json"
            path.touch()
            with path.open("w") as f:
                json.dump(result, f, indent=2)
        else:
            self.logger.debug(f"Workflow result:\n{json.dumps(result, indent=2)}")
        return DispatchWorkflowResult(wf_id=wf_id, final_context=result)

    def cancel_workflow_execution(
        self,
        wf_exec_id: identifiers.WorkflowExecutionID | identifiers.WorkflowScheduleID,
    ) -> Awaitable[None]:
        """Cancel a workflow execution."""
        return self.handle(wf_exec_id).cancel()

    def terminate_workflow_execution(
        self,
        wf_exec_id: identifiers.WorkflowExecutionID | identifiers.WorkflowScheduleID,
        reason: str | None = None,
    ) -> Awaitable[None]:
        """Terminate a workflow execution."""
        return self.handle(wf_exec_id).terminate(reason=reason)
