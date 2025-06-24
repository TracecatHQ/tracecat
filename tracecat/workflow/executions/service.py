from __future__ import annotations

import asyncio
import datetime
import json
from collections import OrderedDict
from collections.abc import AsyncGenerator, Awaitable, Sequence
from typing import Any

from temporalio.api.enums.v1 import EventType
from temporalio.api.history.v1 import HistoryEvent
from temporalio.client import (
    Client,
    WorkflowExecution,
    WorkflowFailureError,
    WorkflowHandle,
    WorkflowHistoryEventFilterType,
)
from temporalio.common import (
    SearchAttributeKey,
    SearchAttributePair,
    TypedSearchAttributes,
)
from temporalio.service import RPCError

from tracecat import config
from tracecat.contexts import ctx_role
from tracecat.db.schemas import Interaction
from tracecat.dsl.client import get_temporal_client
from tracecat.dsl.common import RETRY_POLICIES, DSLInput, DSLRunArgs
from tracecat.dsl.models import Task, TriggerInputs
from tracecat.dsl.validation import validate_trigger_inputs
from tracecat.dsl.workflow import DSLWorkflow
from tracecat.ee.interactions.models import InteractionInput
from tracecat.ee.interactions.service import InteractionService
from tracecat.identifiers import UserID
from tracecat.identifiers.workflow import (
    WorkflowExecutionID,
    WorkflowID,
    generate_exec_id,
)
from tracecat.logger import logger
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatValidationError
from tracecat.workflow.executions.common import (
    HISTORY_TO_WF_EVENT_TYPE,
    build_query,
    extract_first,
    get_result,
    get_source_event_id,
    is_close_event,
    is_error_event,
    is_scheduled_event,
    is_start_event,
)
from tracecat.workflow.executions.enums import (
    TemporalSearchAttr,
    TriggerType,
    WorkflowEventType,
    WorkflowExecutionEventStatus,
)
from tracecat.workflow.executions.models import (
    EventFailure,
    EventGroup,
    WorkflowDispatchResponse,
    WorkflowExecutionCreateResponse,
    WorkflowExecutionEvent,
    WorkflowExecutionEventCompact,
)


class WorkflowExecutionsService:
    """Workflow executions service."""

    def __init__(self, client: Client, role: Role | None = None):
        self.role = role or ctx_role.get()
        self._client = client
        self.logger = logger.bind(service="workflow_executions")

    @staticmethod
    async def connect(role: Role | None = None) -> WorkflowExecutionsService:
        """Initialize and connect to the service."""
        client = await get_temporal_client()
        return WorkflowExecutionsService(client=client, role=role)

    def handle(
        self, wf_exec_id: WorkflowExecutionID
    ) -> WorkflowHandle[DSLWorkflow, DSLRunArgs]:
        return self._client.get_workflow_handle_for(DSLWorkflow.run, wf_exec_id)

    async def query_interaction_states(
        self,
        wf_exec_id: WorkflowExecutionID,
    ) -> Sequence[Interaction]:
        """Query the interaction states for a workflow execution."""
        async with InteractionService.with_session(role=self.role) as svc:
            return await svc.list_interactions(wf_exec_id=wf_exec_id)

    async def query_executions(
        self,
        query: str | None = None,
        limit: int | None = None,
        **kwargs: Any,
    ) -> list[WorkflowExecution]:
        """Query workflow executions with optional filtering and limits.

        Args:
            query: Optional query string to filter executions
            limit: Optional maximum number of executions to return
            **kwargs: Additional arguments passed to list_workflows

        Returns:
            List of matching WorkflowExecution objects
        """
        if limit is not None and limit <= 0:
            limit = None

        executions = []
        # NOTE: We operate under the assumption that `list_workflows` is ordered by StartTime
        # This appears to be true based on observation
        async for execution in self._client.list_workflows(query=query, **kwargs):
            executions.append(execution)
            if limit and len(executions) >= limit:
                break
        return executions

    async def get_execution(
        self, wf_exec_id: WorkflowExecutionID, _include_legacy: bool = True
    ) -> WorkflowExecution | None:
        self.logger.info("Getting workflow execution", wf_exec_id=wf_exec_id)
        handle = self.handle(wf_exec_id)
        try:
            return await handle.describe()
        except RPCError as e:
            if "not found" in str(e).lower():
                return None
            raise

    async def list_executions(
        self,
        workflow_id: WorkflowID | None = None,
        trigger_types: set[TriggerType] | None = None,
        triggered_by_user_id: UserID | None = None,
        limit: int | None = None,
    ) -> list[WorkflowExecution]:
        """List all workflow executions."""
        query = build_query(
            workflow_id=workflow_id,
            trigger_types=trigger_types,
            triggered_by_user_id=triggered_by_user_id,
        )
        return await self.query_executions(query=query, limit=limit)

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

    async def list_workflow_execution_events_compact(
        self,
        wf_exec_id: WorkflowExecutionID,
        **kwargs,
    ) -> list[WorkflowExecutionEventCompact]:
        """List the event history of a workflow execution."""
        # Mapping of source event ID to compact event
        # Source event id is the event ID of the scheduled event
        # Position -> WFECompact
        id2event: OrderedDict[int, WorkflowExecutionEventCompact] = OrderedDict()

        async for event in self.handle(wf_exec_id).fetch_history_events(**kwargs):
            if is_scheduled_event(event):
                # Create a new source event
                source = WorkflowExecutionEventCompact.from_source_event(event)
                if source is None:
                    logger.trace(
                        "Skipping scheduled event as there is no source",
                        event_id=event.event_id,
                    )
                    continue
                id2event[event.event_id] = source
            else:
                logger.trace("Processing event", event_type=event.event_type)
                source_id = get_source_event_id(event)
                if source_id is None:
                    logger.trace(
                        "Event has no source event ID, skipping",
                        source_id=source_id,
                        event_id=event.event_id,
                    )
                    continue
                source = id2event.get(source_id)
                if not source:
                    logger.trace(
                        "Source event not found, skipping",
                        event_id=event.event_id,
                    )
                    continue
                wf_event_type = HISTORY_TO_WF_EVENT_TYPE[event.event_type]
                source.curr_event_type = wf_event_type
                if source.status != WorkflowExecutionEventStatus.DETACHED:
                    # Only overwrite the status if it's not already set to DETACHED
                    # If it's DETACHED the status remains unchanged
                    source.status = wf_event_type.to_status()
                if is_start_event(event):
                    source.start_time = event.event_time.ToDatetime(datetime.UTC)
                if is_close_event(event):
                    source.close_time = event.event_time.ToDatetime(datetime.UTC)
                    source.action_result = get_result(event)
                if is_error_event(event):
                    source.action_error = EventFailure.from_history_event(event)
        # For the resultant values, if there are duplicate source action_refs,
        #  we should merge them into a single event.
        task2events: dict[Task, WorkflowExecutionEventCompact] = {}
        for event in id2event.values():
            task = Task(ref=event.action_ref, stream_id=event.stream_id)
            if task in task2events:
                group_event = task2events[task]
                group_event.child_wf_count += 1
                # Take the min start time
                if group_event.start_time and event.start_time:
                    group_event.start_time = min(
                        group_event.start_time, event.start_time
                    )
                if group_event.schedule_time and event.schedule_time:
                    group_event.schedule_time = min(
                        group_event.schedule_time, event.schedule_time
                    )
                if group_event.close_time and event.close_time:
                    group_event.close_time = max(
                        group_event.close_time, event.close_time
                    )
                if group_event.action_result and isinstance(
                    group_event.action_result, list
                ):
                    group_event.action_result.append(event.action_result)
            else:
                task2events[task] = event
                # There's an edge case where a direct child wf invocation and a single looped child wf invocation
                # is ambiguous - how do we tell whether we should wrap the result in a list or not?
                # We use Temporal memo to store the loop index, so we can detect this case
                # If the loop index is None, it means it was a direct child wf invocation
                # Otherwise, it was a looped child wf invocation
                if event.loop_index is not None:
                    task2events[task].action_result = [event.action_result]

        return list(task2events.values())

    async def list_workflow_execution_events(
        self,
        wf_exec_id: WorkflowExecutionID,
        event_filter_type: WorkflowHistoryEventFilterType = WorkflowHistoryEventFilterType.ALL_EVENT,
        **kwargs,
    ) -> list[WorkflowExecutionEvent]:
        """List the event history of a workflow execution."""

        history = await self.handle(wf_exec_id).fetch_history(
            event_filter_type=event_filter_type, **kwargs
        )
        event_group_names: dict[int, EventGroup | None] = {}
        events: list[WorkflowExecutionEvent] = []
        for event in history.events:
            match event.event_type:
                # === Child Workflow Execution Events ===
                case EventType.EVENT_TYPE_START_CHILD_WORKFLOW_EXECUTION_INITIATED:
                    group = EventGroup.from_initiated_child_workflow(event)
                    event_group_names[event.event_id] = group
                    events.append(
                        WorkflowExecutionEvent(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(datetime.UTC),
                            event_type=WorkflowEventType.START_CHILD_WORKFLOW_EXECUTION_INITIATED,
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
                        WorkflowExecutionEvent(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(datetime.UTC),
                            event_type=WorkflowEventType.CHILD_WORKFLOW_EXECUTION_STARTED,
                            event_group=group,
                            task_id=event.task_id,
                        )
                    )
                case EventType.EVENT_TYPE_CHILD_WORKFLOW_EXECUTION_COMPLETED:
                    result = extract_first(
                        event.child_workflow_execution_completed_event_attributes.result
                    )
                    initiator_event_id = event.child_workflow_execution_completed_event_attributes.initiated_event_id
                    group = event_group_names.get(initiator_event_id)
                    events.append(
                        WorkflowExecutionEvent(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(datetime.UTC),
                            event_type=WorkflowEventType.CHILD_WORKFLOW_EXECUTION_COMPLETED,
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
                        WorkflowExecutionEvent(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(datetime.UTC),
                            event_type=WorkflowEventType.CHILD_WORKFLOW_EXECUTION_FAILED,
                            event_group=group,
                            task_id=event.task_id,
                            failure=EventFailure.from_history_event(event),
                        )
                    )

                # === Workflow Execution Events ===
                case EventType.EVENT_TYPE_WORKFLOW_EXECUTION_STARTED:
                    attrs = event.workflow_execution_started_event_attributes
                    run_args_data = extract_first(attrs.input)
                    dsl_run_args = DSLRunArgs(**run_args_data)
                    # Empty strings coerce to None
                    parent_exec_id = attrs.parent_workflow_execution.workflow_id or None
                    events.append(
                        WorkflowExecutionEvent(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(datetime.UTC),
                            event_type=WorkflowEventType.WORKFLOW_EXECUTION_STARTED,
                            parent_wf_exec_id=parent_exec_id,
                            task_id=event.task_id,
                            role=dsl_run_args.role,
                            workflow_timeout=dsl_run_args.runtime_config.timeout,
                        )
                    )
                case EventType.EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED:
                    result = extract_first(
                        event.workflow_execution_completed_event_attributes.result
                    )
                    events.append(
                        WorkflowExecutionEvent(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(datetime.UTC),
                            event_type=WorkflowEventType.WORKFLOW_EXECUTION_COMPLETED,
                            task_id=event.task_id,
                            result=result,
                        )
                    )
                case EventType.EVENT_TYPE_WORKFLOW_EXECUTION_FAILED:
                    events.append(
                        WorkflowExecutionEvent(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(datetime.UTC),
                            event_type=WorkflowEventType.WORKFLOW_EXECUTION_FAILED,
                            task_id=event.task_id,
                            failure=EventFailure.from_history_event(event),
                        )
                    )
                case EventType.EVENT_TYPE_WORKFLOW_EXECUTION_TERMINATED:
                    events.append(
                        WorkflowExecutionEvent(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(datetime.UTC),
                            event_type=WorkflowEventType.WORKFLOW_EXECUTION_TERMINATED,
                            task_id=event.task_id,
                        )
                    )
                case EventType.EVENT_TYPE_WORKFLOW_EXECUTION_CANCELED:
                    events.append(
                        WorkflowExecutionEvent(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(datetime.UTC),
                            event_type=WorkflowEventType.WORKFLOW_EXECUTION_CANCELED,
                            task_id=event.task_id,
                        )
                    )
                case EventType.EVENT_TYPE_WORKFLOW_EXECUTION_CONTINUED_AS_NEW:
                    events.append(
                        WorkflowExecutionEvent(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(datetime.UTC),
                            event_type=WorkflowEventType.WORKFLOW_EXECUTION_CONTINUED_AS_NEW,
                            task_id=event.task_id,
                        )
                    )
                case EventType.EVENT_TYPE_WORKFLOW_EXECUTION_TIMED_OUT:
                    events.append(
                        WorkflowExecutionEvent(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(datetime.UTC),
                            event_type=WorkflowEventType.WORKFLOW_EXECUTION_TIMED_OUT,
                            task_id=event.task_id,
                        )
                    )
                # === Activity Task Events ===
                case EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED:
                    if not (group := EventGroup.from_scheduled_activity(event)):
                        continue
                    event_group_names[event.event_id] = group
                    events.append(
                        WorkflowExecutionEvent(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(datetime.UTC),
                            event_type=WorkflowEventType.ACTIVITY_TASK_SCHEDULED,
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
                        WorkflowExecutionEvent(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(datetime.UTC),
                            event_type=WorkflowEventType.ACTIVITY_TASK_STARTED,
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
                    result = extract_first(
                        event.activity_task_completed_event_attributes.result
                    )
                    events.append(
                        WorkflowExecutionEvent(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(datetime.UTC),
                            event_type=WorkflowEventType.ACTIVITY_TASK_COMPLETED,
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
                        WorkflowExecutionEvent(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(datetime.UTC),
                            event_type=WorkflowEventType.ACTIVITY_TASK_FAILED,
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
                        WorkflowExecutionEvent(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(datetime.UTC),
                            event_type=WorkflowEventType.ACTIVITY_TASK_TIMED_OUT,
                            task_id=event.task_id,
                            event_group=group,
                        )
                    )
                # === Workflow Execution Interaction Events ===
                case EventType.EVENT_TYPE_WORKFLOW_EXECUTION_SIGNALED:
                    attrs = event.workflow_execution_signaled_event_attributes
                    data = extract_first(attrs.input)
                    events.append(
                        WorkflowExecutionEvent(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(datetime.UTC),
                            event_type=WorkflowEventType.WORKFLOW_EXECUTION_SIGNALED,
                            task_id=event.task_id,
                            result=InteractionInput(**data),
                        )
                    )
                case EventType.EVENT_TYPE_WORKFLOW_EXECUTION_UPDATE_ACCEPTED:
                    group = EventGroup.from_accepted_workflow_update(event)
                    event_group_names[event.event_id] = group
                    events.append(
                        WorkflowExecutionEvent(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(datetime.UTC),
                            event_type=WorkflowEventType.WORKFLOW_EXECUTION_UPDATE_ACCEPTED,
                            event_group=group,
                            task_id=event.task_id,
                        )
                    )
                case EventType.EVENT_TYPE_WORKFLOW_EXECUTION_UPDATE_REJECTED:
                    # TODO: Handle this
                    logger.warning(
                        "Received a workflow execution update rejected event",
                        event_id=event.event_id,
                        event_type=event.event_type,
                    )
                case EventType.EVENT_TYPE_WORKFLOW_EXECUTION_UPDATE_COMPLETED:
                    attrs = event.workflow_execution_update_completed_event_attributes
                    parent_event_id = attrs.accepted_event_id
                    if not (group := event_group_names.get(parent_event_id)):
                        logger.warning(
                            "Received a workflow execution update completed event with an unexpected parent event id",
                            event_id=event.event_id,
                            parent_event_id=parent_event_id,
                        )
                        continue
                    event_group_names[event.event_id] = group
                    outcome = attrs.outcome
                    if outcome.HasField("success"):
                        result = extract_first(outcome.success)
                        events.append(
                            WorkflowExecutionEvent(
                                event_id=event.event_id,
                                event_time=event.event_time.ToDatetime(datetime.UTC),
                                event_type=WorkflowEventType.WORKFLOW_EXECUTION_UPDATE_COMPLETED,
                                event_group=group,
                                task_id=event.task_id,
                                result=result,
                            )
                        )
                    elif outcome.HasField("failure"):
                        events.append(
                            WorkflowExecutionEvent(
                                event_id=event.event_id,
                                event_time=event.event_time.ToDatetime(datetime.UTC),
                                event_type=WorkflowEventType.WORKFLOW_EXECUTION_UPDATE_COMPLETED,
                                event_group=group,
                                task_id=event.task_id,
                                failure=EventFailure.from_history_event(event),
                            )
                        )
                case _:
                    logger.trace("Unhandled event type", event_type=event.event_type)
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
        trigger_type: TriggerType = TriggerType.MANUAL,
    ) -> WorkflowExecutionCreateResponse:
        """Create a new workflow execution.

        Note: This method schedules the workflow execution and returns immediately.
        """
        wf_exec_id = generate_exec_id(wf_id)
        coro = self.create_workflow_execution(
            dsl=dsl,
            wf_id=wf_id,
            payload=payload,
            trigger_type=trigger_type,
            wf_exec_id=wf_exec_id,
        )
        _ = asyncio.create_task(coro)
        return WorkflowExecutionCreateResponse(
            message="Workflow execution started",
            wf_id=wf_id,
            wf_exec_id=wf_exec_id,
        )

    async def create_workflow_execution(
        self,
        dsl: DSLInput,
        *,
        wf_id: WorkflowID,
        payload: TriggerInputs | None = None,
        trigger_type: TriggerType = TriggerType.MANUAL,
        wf_exec_id: WorkflowExecutionID | None = None,
    ) -> WorkflowDispatchResponse:
        """Create a new workflow execution.

        Note: This method blocks until the workflow execution completes.
        """
        validation_result = validate_trigger_inputs(dsl=dsl, payload=payload)
        if validation_result.status == "error":
            logger.error(validation_result.msg, detail=validation_result.detail)
            raise TracecatValidationError(
                validation_result.msg, detail=validation_result.detail
            )
        if wf_exec_id is None:
            wf_exec_id = generate_exec_id(wf_id)

        return await self._dispatch_workflow(
            dsl=dsl,
            wf_id=wf_id,
            wf_exec_id=wf_exec_id,
            trigger_inputs=payload,
            trigger_type=trigger_type,
        )

    async def _dispatch_workflow(
        self,
        dsl: DSLInput,
        wf_id: WorkflowID,
        wf_exec_id: WorkflowExecutionID,
        trigger_inputs: TriggerInputs | None = None,
        trigger_type: TriggerType = TriggerType.MANUAL,
        **kwargs: Any,
    ) -> WorkflowDispatchResponse:
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
            trigger_type=trigger_type,
        )

        pairs = [trigger_type.to_temporal_search_attr_pair()]
        if self.role.user_id is not None:
            pairs.append(
                SearchAttributePair(
                    key=SearchAttributeKey.for_keyword(
                        TemporalSearchAttr.TRIGGERED_BY_USER_ID.value
                    ),
                    value=str(self.role.user_id),
                )
            )
        search_attrs = TypedSearchAttributes(search_attributes=pairs)
        try:
            result = await self._client.execute_workflow(
                DSLWorkflow.run,
                DSLRunArgs(
                    dsl=dsl, role=self.role, wf_id=wf_id, trigger_inputs=trigger_inputs
                ),
                id=wf_exec_id,
                task_queue=config.TEMPORAL__CLUSTER_QUEUE,
                retry_policy=RETRY_POLICIES["workflow:fail_fast"],
                # We don't currently differentiate between exec and run timeout as we fail fast for workflows
                execution_timeout=datetime.timedelta(seconds=dsl.config.timeout),
                search_attributes=search_attrs,
                **kwargs,
            )
        except WorkflowFailureError as e:
            self.logger.error(
                str(e), role=self.role, wf_exec_id=wf_exec_id, cause=e.cause
            )
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
        return WorkflowDispatchResponse(wf_id=wf_id, result=result)

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
