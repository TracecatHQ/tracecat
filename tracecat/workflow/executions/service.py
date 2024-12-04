from __future__ import annotations

import asyncio
import datetime
import json
from collections.abc import AsyncGenerator, Awaitable, Coroutine
from typing import Any

import orjson
import temporalio.api.common.v1
from temporalio.api.enums.v1 import EventType
from temporalio.api.history.v1 import HistoryEvent
from temporalio.client import (
    Client,
    WorkflowExecution,
    WorkflowExecutionDescription,
    WorkflowExecutionStatus,
    WorkflowFailureError,
    WorkflowHandle,
    WorkflowHistoryEventFilterType,
)
from temporalio.service import RPCError

from tracecat import config
from tracecat.contexts import ctx_role
from tracecat.dsl.client import get_temporal_client
from tracecat.dsl.common import DSLInput, DSLRunArgs
from tracecat.dsl.models import TriggerInputs
from tracecat.dsl.validation import validate_trigger_inputs
from tracecat.dsl.workflow import DSLWorkflow, retry_policies
from tracecat.identifiers.workflow import WorkflowExecutionID, WorkflowID, exec_id
from tracecat.logger import logger
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

    def handle(self, wf_exec_id: WorkflowExecutionID) -> WorkflowHandle:
        return self._client.get_workflow_handle(wf_exec_id)

    async def query_executions(
        self, query: str | None = None, **kwargs
    ) -> list[WorkflowExecution]:
        # Invoke with async for
        return [
            wf_exec
            async for wf_exec in self._client.list_workflows(query=query, **kwargs)
        ]

    async def get_execution(
        self, wf_exec_id: WorkflowExecutionID
    ) -> WorkflowExecutionDescription:
        return await self.handle(wf_exec_id).describe()

    async def get_execution_status(
        self, wf_exec_id: WorkflowExecutionID
    ) -> WorkflowExecutionStatus | None:
        """Get the status of a workflow execution."""

        description = await self.handle(wf_exec_id).describe()
        return description.status if description else None

    async def list_executions(self) -> list[WorkflowExecution]:
        """List all workflow executions."""

        return await self.query_executions()

    async def list_executions_by_workflow_id(
        self, wf_id: WorkflowID
    ) -> list[WorkflowExecution]:
        """List all workflow executions by workflow ID."""

        query = f"WorkflowId STARTS_WITH {wf_id!r}"
        return await self.query_executions(query=query)

    async def get_latest_execution_by_workflow_id(
        self, wf_id: WorkflowID
    ) -> WorkflowExecution:
        """Get the latest workflow execution by workflow ID."""

        executions = await self.list_executions_by_workflow_id(wf_id)
        return max(executions, key=lambda exec: exec.start_time)

    async def list_workflow_execution_event_history(
        self,
        wf_exec_id: WorkflowExecutionID,
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
                # === Child Workflow Execution Events ===
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
                    result = _extract_first(
                        event.child_workflow_execution_completed_event_attributes.result
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

                # === Workflow Execution Events ===
                case EventType.EVENT_TYPE_WORKFLOW_EXECUTION_STARTED:
                    attrs = event.workflow_execution_started_event_attributes
                    run_args_data = _extract_first(attrs.input)
                    dsl_run_args = DSLRunArgs(**run_args_data)
                    # Empty strings coerce to None
                    parent_exec_id = attrs.parent_workflow_execution.workflow_id or None
                    events.append(
                        EventHistoryResponse(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(datetime.UTC),
                            event_type=EventHistoryType.WORKFLOW_EXECUTION_STARTED,
                            parent_wf_exec_id=parent_exec_id,
                            task_id=event.task_id,
                            role=dsl_run_args.role,
                            workflow_timeout=dsl_run_args.runtime_config.timeout,
                        )
                    )
                case EventType.EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED:
                    result = _extract_first(
                        event.workflow_execution_completed_event_attributes.result
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
                case EventType.EVENT_TYPE_WORKFLOW_EXECUTION_CONTINUED_AS_NEW:
                    events.append(
                        EventHistoryResponse(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(datetime.UTC),
                            event_type=EventHistoryType.WORKFLOW_EXECUTION_CONTINUED_AS_NEW,
                            task_id=event.task_id,
                        )
                    )
                case EventType.EVENT_TYPE_WORKFLOW_EXECUTION_TIMED_OUT:
                    events.append(
                        EventHistoryResponse(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(datetime.UTC),
                            event_type=EventHistoryType.WORKFLOW_EXECUTION_TIMED_OUT,
                            task_id=event.task_id,
                        )
                    )
                # === Activity Task Events ===
                case EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED:
                    if not (group := EventGroup.from_scheduled_activity(event)):
                        continue
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
                    attrs = event.activity_task_started_event_attributes
                    parent_event_id = attrs.scheduled_event_id
                    if not (group := event_group_names.get(parent_event_id)):
                        continue
                    event_group_names[event.event_id] = group.model_copy(
                        update={"current_attempt": attrs.attempt}
                    )
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
                    if not (group := event_group_names.get(gparent_event_id)):
                        continue
                    event_group_names[event.event_id] = group
                    result = _extract_first(
                        event.activity_task_completed_event_attributes.result
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
                    if not (group := event_group_names.get(gparent_event_id)):
                        continue
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
                case EventType.EVENT_TYPE_ACTIVITY_TASK_TIMED_OUT:
                    gparent_event_id = event.activity_task_timed_out_event_attributes.scheduled_event_id
                    if not (group := event_group_names.get(gparent_event_id)):
                        continue
                    events.append(
                        EventHistoryResponse(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(datetime.UTC),
                            event_type=EventHistoryType.ACTIVITY_TASK_TIMED_OUT,
                            task_id=event.task_id,
                            event_group=group,
                        )
                    )
                case _:
                    logger.debug("Unhandled event type", event_type=event.event_type)
                    continue
        return events

    async def iter_list_workflow_execution_event_history(
        self,
        wf_exec_id: WorkflowExecutionID,
        event_filter_type: WorkflowHistoryEventFilterType = WorkflowHistoryEventFilterType.ALL_EVENT,
        **kwargs,
    ) -> AsyncGenerator[HistoryEvent, Any]:
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
        wf_id: WorkflowID,
        payload: TriggerInputs | None = None,
    ) -> CreateWorkflowExecutionResponse:
        """Create a new workflow execution.

        Note: This method schedules the workflow execution and returns immediately.
        """
        coro = self.create_workflow_execution(dsl=dsl, wf_id=wf_id, payload=payload)
        _ = asyncio.create_task(coro)
        return CreateWorkflowExecutionResponse(
            message="Workflow execution started",
            wf_id=wf_id,
            wf_exec_id=exec_id(wf_id),
        )

    def create_workflow_execution(
        self,
        dsl: DSLInput,
        *,
        wf_id: WorkflowID,
        payload: TriggerInputs | None = None,
    ) -> Coroutine[Any, Any, DispatchWorkflowResult]:
        """Create a new workflow execution.

        Note: This method blocks until the workflow execution completes.
        """
        validation_result = validate_trigger_inputs(dsl=dsl, payload=payload)
        if validation_result.status == "error":
            logger.error(validation_result.msg, detail=validation_result.detail)
            raise TracecatValidationError(
                validation_result.msg, detail=validation_result.detail
            )

        wf_exec_id = exec_id(wf_id)
        return self._dispatch_workflow(
            dsl=dsl,
            wf_id=wf_id,
            wf_exec_id=wf_exec_id,
            trigger_inputs=payload,
        )

    async def _dispatch_workflow(
        self,
        dsl: DSLInput,
        wf_id: WorkflowID,
        wf_exec_id: WorkflowExecutionID,
        trigger_inputs: TriggerInputs | None = None,
        **kwargs: Any,
    ) -> DispatchWorkflowResult:
        if rpc_timeout := config.TEMPORAL__CLIENT_RPC_TIMEOUT:
            kwargs["rpc_timeout"] = datetime.timedelta(seconds=float(rpc_timeout))
        if task_timeout := config.TEMPORAL__TASK_TIMEOUT:
            kwargs.setdefault(
                "task_timeout", datetime.timedelta(seconds=float(task_timeout))
            )

        logger.info(
            f"Executing DSL workflow: {dsl.title}",
            role=self.role,
            wf_exec_id=wf_exec_id,
            run_config=dsl.config,
            kwargs=kwargs,
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
                # We don't currently differentiate between exec and run timeout as we fail fast for workflows
                execution_timeout=datetime.timedelta(seconds=dsl.config.timeout),
                **kwargs,
            )
        except WorkflowFailureError as e:
            self.logger.error(str(e), role=self.role, wf_exec_id=wf_exec_id, e=e)
            raise e
        except RPCError as e:
            self.logger.error(
                f"Temporal service RPC error occurred while executing the workflow: {e}",
                role=self.role,
                wf_exec_id=wf_exec_id,
                e=e,
            )
            raise e

        except Exception as e:
            self.logger.exception(
                "Unexpected workflow error", role=self.role, wf_exec_id=wf_exec_id, e=e
            )
            raise e
        self.logger.debug(f"Workflow result:\n{json.dumps(result, indent=2)}")
        return DispatchWorkflowResult(wf_id=wf_id, final_context=result)

    def cancel_workflow_execution(
        self, wf_exec_id: WorkflowExecutionID
    ) -> Awaitable[None]:
        """Cancel a workflow execution."""
        return self.handle(wf_exec_id).cancel()

    def terminate_workflow_execution(
        self, wf_exec_id: WorkflowExecutionID, reason: str | None = None
    ) -> Awaitable[None]:
        """Terminate a workflow execution."""
        return self.handle(wf_exec_id).terminate(reason=reason)


def _extract_first(input_or_result: temporalio.api.common.v1.Payloads) -> Any:
    """Extract the first payload from a workflow history event."""
    raw_data = input_or_result.payloads[0].data
    try:
        return orjson.loads(raw_data)
    except orjson.JSONDecodeError as e:
        logger.warning(
            "Failed to decode JSON data, attemping to decode as string",
            raw_data=raw_data,
            e=e,
        )

    try:
        return raw_data.decode()
    except UnicodeDecodeError:
        logger.warning("Failed to decode data as string, returning raw bytes")
        return raw_data
