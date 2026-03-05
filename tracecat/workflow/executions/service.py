from __future__ import annotations

import asyncio
import datetime
import json
from collections import OrderedDict, deque
from collections.abc import AsyncGenerator, Awaitable, Sequence
from typing import Any

import temporalio.api.enums.v1
from pydantic import ValidationError
from temporalio.api.enums.v1 import EventType, PendingActivityState
from temporalio.api.history.v1 import HistoryEvent
from temporalio.client import (
    Client,
    WorkflowExecution,
    WorkflowFailureError,
    WorkflowHandle,
    WorkflowHistoryEventFilterType,
)
from temporalio.common import TypedSearchAttributes
from temporalio.exceptions import TerminatedError
from temporalio.service import RPCError

from tracecat import config
from tracecat.audit.logger import audit_log
from tracecat.auth.types import Role
from tracecat.contexts import ctx_role
from tracecat.db.models import Interaction
from tracecat.dsl.client import get_temporal_client
from tracecat.dsl.common import (
    RETRY_POLICIES,
    DSLInput,
    DSLRunArgs,
    edge_components_from_dep,
)
from tracecat.dsl.enums import PlatformAction
from tracecat.dsl.schemas import ROOT_STREAM, TaskResult, TriggerInputs
from tracecat.dsl.types import Task
from tracecat.dsl.workflow import DSLWorkflow
from tracecat.ee.interactions.schemas import InteractionInput
from tracecat.ee.interactions.service import InteractionService
from tracecat.identifiers import UserID
from tracecat.identifiers.workflow import (
    WorkflowExecutionID,
    WorkflowID,
    exec_id_to_parts,
    generate_exec_id,
)
from tracecat.logger import logger
from tracecat.registry.lock.types import RegistryLock
from tracecat.storage.collection import (
    get_collection_page as get_storage_collection_page,
)
from tracecat.storage.object import (
    CollectionObject,
    ExternalObject,
    InlineObject,
    StoredObject,
    StoredObjectValidator,
    get_object_storage,
)
from tracecat.workflow.executions.common import (
    HISTORY_TO_WF_EVENT_TYPE,
    build_query,
    extract_first,
    get_result,
    get_source_event_id,
    get_stored_result,
    is_close_event,
    is_error_event,
    is_scheduled_event,
    is_start_event,
)
from tracecat.workflow.executions.constants import WF_COMPLETED_REF, WF_FAILURE_REF
from tracecat.workflow.executions.enums import (
    ExecutionType,
    TemporalSearchAttr,
    TriggerType,
    WorkflowEventType,
    WorkflowExecutionEventStatus,
)
from tracecat.workflow.executions.schemas import (
    EventFailure,
    EventGroup,
    WorkflowDispatchResponse,
    WorkflowExecutionCreateResponse,
    WorkflowExecutionEvent,
    WorkflowExecutionEventCompact,
)
from tracecat.workflow.management.schemas import WorkflowDraftPins
from tracecat.workspaces.service import WorkspaceService


class WorkflowExecutionResultNotFoundError(ValueError):
    """Raised when no matching completed event exists for a given event ID."""


def _unwrap_loop_control_result(value: Any) -> Any:
    """Unwrap loop control result payloads across known envelope shapes."""
    curr = value
    for _ in range(4):
        if not isinstance(curr, dict):
            return curr
        if "result" in curr:
            curr = curr["result"]
            continue
        if "data" in curr:
            curr = curr["data"]
            continue
        return curr
    return curr


def _extract_while_metadata(
    action_name: str, action_result: Any
) -> tuple[int | None, bool | None]:
    """Extract do-while metadata while keeping `loop_index` semantics separate."""
    payload = _unwrap_loop_control_result(action_result)
    if not isinstance(payload, dict):
        return None, None

    if action_name == PlatformAction.LOOP_START.value:
        iteration = payload.get("iteration")
        return (iteration if isinstance(iteration, int) else None), None
    if action_name == PlatformAction.LOOP_END.value:
        should_continue = payload.get("continue")
        return None, (should_continue if isinstance(should_continue, bool) else None)
    return None, None


class WorkflowExecutionsService:
    """Workflow executions service."""

    def __init__(self, client: Client, role: Role | None = None):
        self.role = role or ctx_role.get()
        self._client = client
        self.logger = logger.bind(service="workflow_executions")

    @staticmethod
    def format_failure_cause(cause: BaseException | None) -> str:
        """Return the most specific nested cause message for Temporal failures."""
        if cause is None:
            return "Unknown workflow failure"

        current: BaseException = cause
        seen: set[int] = set()
        while True:
            seen.add(id(current))
            nested = getattr(current, "cause", None)
            if not isinstance(nested, BaseException):
                break
            if id(nested) in seen:
                break
            current = nested

        message = str(current)
        if message:
            return message
        return str(cause) or current.__class__.__name__

    @staticmethod
    def _pin_event_sort_key(
        event: WorkflowExecutionEventCompact,
    ) -> tuple[bool, datetime.datetime, int]:
        return (
            event.stream_id == ROOT_STREAM,
            event.close_time or event.start_time or event.schedule_time,
            event.source_event_id,
        )

    @staticmethod
    def _coerce_pinned_task_result(result: Any) -> TaskResult:
        if isinstance(result, TaskResult):
            return result
        try:
            return TaskResult.model_validate(result)
        except ValidationError:
            pass
        try:
            stored = StoredObjectValidator.validate_python(result)
        except ValidationError:
            return TaskResult.from_result(result)

        match stored:
            case InlineObject(data=data):
                result_typename = stored.typename or type(data).__name__
            case CollectionObject():
                result_typename = stored.typename or "list"
            case ExternalObject():
                result_typename = stored.typename or "external"

        return TaskResult(
            result=stored,
            result_typename=result_typename,
        )

    @staticmethod
    def parse_draft_pins(
        draft_pins: dict[str, Any] | None,
    ) -> WorkflowDraftPins | None:
        if not draft_pins:
            return None
        try:
            return WorkflowDraftPins.model_validate(draft_pins)
        except ValidationError:
            return None

    @classmethod
    def _select_latest_completed_events_by_ref(
        cls,
        events: Sequence[WorkflowExecutionEventCompact],
        target_refs: set[str],
    ) -> dict[str, WorkflowExecutionEventCompact]:
        selected_events: dict[str, WorkflowExecutionEventCompact] = {}
        for event in events:
            if event.action_ref not in target_refs:
                continue
            if event.status != WorkflowExecutionEventStatus.COMPLETED:
                continue
            if event.action_error is not None:
                continue
            existing = selected_events.get(event.action_ref)
            if existing is None or cls._pin_event_sort_key(
                event
            ) > cls._pin_event_sort_key(existing):
                selected_events[event.action_ref] = event
        return selected_events

    async def _get_start_run_context(
        self, wf_exec_id: WorkflowExecutionID
    ) -> tuple[DSLRunArgs, datetime.datetime] | None:
        handle = self.handle(wf_exec_id)
        async for event in handle.fetch_history_events():
            if event.event_type != EventType.EVENT_TYPE_WORKFLOW_EXECUTION_STARTED:
                continue
            attrs = event.workflow_execution_started_event_attributes
            run_args_data = await extract_first(attrs.input)
            started_at = event.event_time.ToDatetime(datetime.UTC)
            try:
                return DSLRunArgs(**run_args_data), started_at
            except ValidationError as e:
                self.logger.warning(
                    "Failed to parse workflow start args",
                    wf_exec_id=wf_exec_id,
                    error=e,
                )
                return None
        return None

    @staticmethod
    def _compute_dag_ref_order(dsl: DSLInput | None) -> dict[str, int]:
        if dsl is None or not dsl.actions:
            return {}

        action_refs = [action.ref for action in dsl.actions]
        ref_set = set(action_refs)
        ref_position = {ref: idx for idx, ref in enumerate(action_refs)}
        indegrees = dict.fromkeys(action_refs, 0)
        adjacency: dict[str, set[str]] = {ref: set() for ref in action_refs}

        for action in dsl.actions:
            for dep_ref in action.depends_on:
                source_ref, _edge_type = edge_components_from_dep(dep_ref)
                if source_ref not in ref_set:
                    continue
                if action.ref in adjacency[source_ref]:
                    continue
                adjacency[source_ref].add(action.ref)
                indegrees[action.ref] += 1

        queue: deque[str] = deque(
            ref for ref in action_refs if indegrees.get(ref, 0) == 0
        )
        sorted_refs: list[str] = []
        while queue:
            ref = queue.popleft()
            sorted_refs.append(ref)
            for child_ref in sorted(adjacency[ref], key=ref_position.__getitem__):
                indegrees[child_ref] -= 1
                if indegrees[child_ref] == 0:
                    queue.append(child_ref)

        if len(sorted_refs) != len(action_refs):
            already_sorted = set(sorted_refs)
            for ref in action_refs:
                if ref not in already_sorted:
                    sorted_refs.append(ref)

        return {ref: idx for idx, ref in enumerate(sorted_refs)}

    @classmethod
    def _order_compact_events_by_dag(
        cls,
        events: list[WorkflowExecutionEventCompact],
        dsl: DSLInput | None,
    ) -> list[WorkflowExecutionEventCompact]:
        dag_order = cls._compute_dag_ref_order(dsl)
        if not dag_order:
            return events

        fallback_rank = len(dag_order) + 1
        indexed = list(enumerate(events))
        indexed.sort(
            key=lambda item: (
                dag_order.get(item[1].action_ref, fallback_rank),
                item[0],
            )
        )
        return [event for _, event in indexed]

    async def _stitch_pinned_compact_events(
        self,
        *,
        wf_exec_id: WorkflowExecutionID,
        compact_events: list[WorkflowExecutionEventCompact],
    ) -> list[WorkflowExecutionEventCompact]:
        run_context = await self._get_start_run_context(wf_exec_id)
        if run_context is None:
            return compact_events

        run_args, run_started_at = run_context
        if not run_args.pinned_action_results:
            return compact_events

        pinned_refs = set(run_args.pinned_action_results)
        if not pinned_refs:
            return compact_events

        existing_refs = {event.action_ref for event in compact_events}
        refs_to_stitch = sorted(pinned_refs - existing_refs)
        if not refs_to_stitch:
            return compact_events

        source_execution_id = run_args.pinned_source_execution_id
        if source_execution_id is None:
            self.logger.debug(
                "Pinned refs present but no source execution ID on run args",
                wf_exec_id=wf_exec_id,
                pinned_refs=sorted(pinned_refs),
            )
            return compact_events
        if source_execution_id == wf_exec_id:
            return compact_events

        try:
            source_events = await self.list_workflow_execution_events_compact(
                source_execution_id,
                include_pinned_synthetic=False,
            )
        except Exception as e:
            self.logger.warning(
                "Failed to load pinned source execution compact events",
                wf_exec_id=wf_exec_id,
                source_execution_id=source_execution_id,
                error=e,
            )
            return compact_events

        selected_source = self._select_latest_completed_events_by_ref(
            source_events, set(refs_to_stitch)
        )
        if not selected_source:
            return compact_events

        stitched_events = list(compact_events)
        unresolved_refs: list[str] = []
        for ref in refs_to_stitch:
            source_event = selected_source.get(ref)
            if source_event is None:
                unresolved_refs.append(ref)
                continue
            stitched_event = source_event.model_copy(deep=True)
            stitched_event.synthetic_kind = "pinned"
            stitched_event.pinned_source_execution_id = source_execution_id
            stitched_event.pinned_source_event_id = source_event.source_event_id
            stitched_event.schedule_time = run_started_at
            stitched_event.start_time = run_started_at
            stitched_event.close_time = run_started_at
            stitched_event.session = None
            stitched_events.append(stitched_event)

        if unresolved_refs:
            self.logger.warning(
                "Some pinned refs could not be stitched into compact events",
                wf_exec_id=wf_exec_id,
                source_execution_id=source_execution_id,
                unresolved_refs=unresolved_refs,
            )

        return self._order_compact_events_by_dag(stitched_events, run_args.dsl)

    async def resolve_draft_pinned_action_results(
        self,
        *,
        wf_id: WorkflowID,
        dsl: DSLInput,
        draft_pins: dict[str, Any] | None,
    ) -> dict[str, TaskResult]:
        """Best-effort resolve draft pin config to action TaskResults.

        Fail-open behavior:
        - Invalid config returns empty pins.
        - Missing execution/refs return empty or partial pins.
        - Resolution warnings are logged but never raised to callers.
        """
        if not draft_pins:
            return {}

        pins = self.parse_draft_pins(draft_pins)
        if pins is None:
            self.logger.warning("Invalid draft pin config; ignoring")
            return {}

        if not pins.action_refs:
            return {}

        try:
            source_wf_id, _ = exec_id_to_parts(pins.source_execution_id)
        except ValueError as e:
            self.logger.warning(
                "Invalid draft pin source execution ID; ignoring",
                source_execution_id=pins.source_execution_id,
                error=e,
            )
            return {}

        if source_wf_id != wf_id:
            self.logger.warning(
                "Draft pin source execution workflow mismatch; ignoring",
                source_workflow_id=source_wf_id,
                target_workflow_id=wf_id,
                source_execution_id=pins.source_execution_id,
            )
            return {}

        source_execution = await self.get_execution(pins.source_execution_id)
        if source_execution is None:
            self.logger.warning(
                "Draft pin source execution not found or inaccessible; ignoring",
                source_execution_id=pins.source_execution_id,
            )
            return {}

        dsl_refs = {task.ref for task in dsl.actions}
        target_refs = [ref for ref in pins.action_refs if ref in dsl_refs]
        missing_refs = sorted(set(pins.action_refs) - dsl_refs)
        if missing_refs:
            self.logger.warning(
                "Some pinned refs are missing in current draft DSL",
                missing_refs=missing_refs,
                source_execution_id=pins.source_execution_id,
            )
        if not target_refs:
            return {}

        try:
            events = await self.list_workflow_execution_events_compact(
                pins.source_execution_id
            )
        except Exception as e:
            self.logger.warning(
                "Failed to load source execution events for draft pins; ignoring",
                source_execution_id=pins.source_execution_id,
                error=e,
            )
            return {}

        selected_events = self._select_latest_completed_events_by_ref(
            events, set(target_refs)
        )

        pinned_results: dict[str, TaskResult] = {}
        unresolved_refs: list[str] = []
        for ref in target_refs:
            event = selected_events.get(ref)
            if event is None:
                unresolved_refs.append(ref)
                continue
            pinned_results[ref] = self._coerce_pinned_task_result(event.action_result)

        if unresolved_refs:
            self.logger.warning(
                "Could not resolve all pinned refs from source execution",
                unresolved_refs=unresolved_refs,
                source_execution_id=pins.source_execution_id,
            )

        return pinned_results

    @staticmethod
    async def connect(role: Role | None = None) -> WorkflowExecutionsService:
        """Initialize and connect to the service."""
        client = await get_temporal_client()
        return WorkflowExecutionsService(client=client, role=role)

    def _handle_background_task_exception(self, task: asyncio.Task[Any]) -> None:
        """Handle exceptions from background workflow execution tasks."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            self.logger.error(
                "Workflow dispatch task failed",
                exception=str(exc),
            )

    def handle(
        self, wf_exec_id: WorkflowExecutionID
    ) -> WorkflowHandle[DSLWorkflow, StoredObject]:
        return self._client.get_workflow_handle_for(DSLWorkflow.run, wf_exec_id)

    async def _resolve_execution_timeout(
        self, seconds: float | int | None
    ) -> datetime.timedelta | None:
        """Resolve the execution timeout based on workspace settings and DSL config.

        Precedence order:
        1. If workspace unlimited timeout enabled → return None (unlimited)
        2. Else if workspace default seconds > 0 → return timedelta
        3. Else if DSL timeout > 0 → return timedelta
        4. Otherwise → return None (unlimited)

        Args:
            seconds: The timeout in seconds (from DSL config or other source)

        Returns:
            timedelta if timeout should be applied, None for unlimited
        """
        if self.role is not None and (ws_id := self.role.workspace_id) is not None:
            async with WorkspaceService.with_session(role=self.role) as ws_svc:
                workspace = await ws_svc.get_workspace(ws_id)
                if workspace and isinstance(workspace.settings, dict):
                    if bool(
                        workspace.settings.get("workflow_unlimited_timeout_enabled")
                    ):
                        return None
                    ws_default = workspace.settings.get(
                        "workflow_default_timeout_seconds"
                    )
                    if isinstance(ws_default, int) and ws_default > 0:
                        return datetime.timedelta(seconds=ws_default)

        if seconds and seconds > 0:
            return datetime.timedelta(seconds=float(seconds))

        return None

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

    def _role_workspace_id(self) -> str | None:
        if self.role is None or self.role.workspace_id is None:
            return None
        return str(self.role.workspace_id)

    def _is_execution_visible_in_workspace(self, execution: WorkflowExecution) -> bool:
        role_workspace_id = self._role_workspace_id()
        if role_workspace_id is None:
            return True

        execution_workspace_id = execution.typed_search_attributes.get(
            TemporalSearchAttr.WORKSPACE_ID.key
        )
        if execution_workspace_id is None:
            self.logger.warning(
                "Denying access to execution without workspace search attribute",
                wf_exec_id=execution.id,
                role_workspace_id=role_workspace_id,
            )
            return False
        if execution_workspace_id != role_workspace_id:
            self.logger.warning(
                "Denying cross-workspace execution access",
                wf_exec_id=execution.id,
                role_workspace_id=role_workspace_id,
                execution_workspace_id=execution_workspace_id,
            )
            return False
        return True

    async def get_execution(
        self, wf_exec_id: WorkflowExecutionID, _include_legacy: bool = True
    ) -> WorkflowExecution | None:
        self.logger.debug("Getting workflow execution", wf_exec_id=wf_exec_id)
        handle = self.handle(wf_exec_id)
        try:
            execution = await handle.describe()
            if not self._is_execution_visible_in_workspace(execution):
                return None
            return execution
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
        workspace_id = self._role_workspace_id()
        query = build_query(
            workflow_id=workflow_id,
            trigger_types=trigger_types,
            triggered_by_user_id=triggered_by_user_id,
            workspace_id=workspace_id,
        )
        return await self.query_executions(query=query, limit=limit)

    async def list_executions_by_workflow_id(
        self, wf_id: WorkflowID
    ) -> list[WorkflowExecution]:
        """List all workflow executions by workflow ID."""
        workspace_id = self._role_workspace_id()
        query = build_query(workflow_id=wf_id, workspace_id=workspace_id)
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
        *,
        include_pinned_synthetic: bool = False,
        **kwargs,
    ) -> list[WorkflowExecutionEventCompact]:
        """List the event history of a workflow execution."""
        # Mapping of source event ID to compact event
        # Source event id is the event ID of the scheduled event
        # Position -> WFECompact
        id2event: OrderedDict[int, WorkflowExecutionEventCompact] = OrderedDict()
        # Map of activity ID to compact event
        activity2eventid: dict[str, int] = {}

        handle = self.handle(wf_exec_id)

        async for event in handle.fetch_history_events(**kwargs):
            if is_scheduled_event(event):
                # Create a new source event
                source = await WorkflowExecutionEventCompact.from_source_event(event)
                if source is None:
                    logger.trace(
                        "Skipping scheduled event as there is no source",
                        event_id=event.event_id,
                    )
                    continue
                id2event[event.event_id] = source

                # If it's a scheduled activity, track the activity ID
                if (
                    event.event_type
                    == temporalio.api.enums.v1.EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
                ):
                    activity_id = (
                        event.activity_task_scheduled_event_attributes.activity_id
                    )
                    activity2eventid[activity_id] = event.event_id
            # ── synthetic compact event for top-level workflow failure ──
            elif event.event_type is EventType.EVENT_TYPE_WORKFLOW_EXECUTION_FAILED:
                failure = EventFailure.from_history_event(event)
                id2event[event.event_id] = WorkflowExecutionEventCompact(
                    source_event_id=event.event_id,
                    schedule_time=event.event_time.ToDatetime(datetime.UTC),
                    start_time=event.event_time.ToDatetime(datetime.UTC),
                    close_time=event.event_time.ToDatetime(datetime.UTC),
                    curr_event_type=HISTORY_TO_WF_EVENT_TYPE[event.event_type],
                    status=WorkflowExecutionEventStatus.FAILED,
                    action_name=WF_FAILURE_REF,
                    action_ref=WF_FAILURE_REF,
                    action_error=failure,
                )
                continue
            # ── synthetic compact event for top-level workflow completion ──
            elif event.event_type is EventType.EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED:
                id2event[event.event_id] = WorkflowExecutionEventCompact(
                    source_event_id=event.event_id,
                    schedule_time=event.event_time.ToDatetime(datetime.UTC),
                    start_time=event.event_time.ToDatetime(datetime.UTC),
                    close_time=event.event_time.ToDatetime(datetime.UTC),
                    curr_event_type=HISTORY_TO_WF_EVENT_TYPE[event.event_type],
                    status=WorkflowExecutionEventStatus.COMPLETED,
                    action_name=WF_COMPLETED_REF,
                    action_ref=WF_COMPLETED_REF,
                    action_result=await get_result(event),
                )
                continue
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
                    source.action_result = await get_result(event)
                    (
                        source.while_iteration,
                        source.while_continue,
                    ) = _extract_while_metadata(
                        source.action_name, source.action_result
                    )
                if is_error_event(event):
                    source.action_error = EventFailure.from_history_event(event)

        desc = await handle.describe()
        # Iterate over the pending activities and update the source event
        for act in desc.raw_description.pending_activities:
            if source_id := activity2eventid.get(act.activity_id):
                source = id2event.get(source_id)
                if source is None:
                    logger.trace(
                        "Source event not found for pending activity",
                        source_id=source_id,
                        activity_id=act.activity_id,
                    )
                    continue
                if act.state == PendingActivityState.PENDING_ACTIVITY_STATE_STARTED:
                    source.curr_event_type = WorkflowEventType.ACTIVITY_TASK_STARTED
                    source.status = WorkflowExecutionEventStatus.STARTED
                    if act.last_started_time:
                        source.start_time = act.last_started_time.ToDatetime(
                            datetime.UTC
                        )
                else:
                    state_name = PendingActivityState.Name(act.state)
                    logger.trace(
                        "Skipping pending activity state update",
                        activity_id=act.activity_id,
                        pending_state=state_name,
                    )
            else:
                logger.trace(
                    "Pending activity without matching source event",
                    activity_id=act.activity_id,
                )

        task2events: dict[Task, WorkflowExecutionEventCompact] = {}
        for event in id2event.values():
            task = Task(ref=event.action_ref, stream_id=event.stream_id)
            if task in task2events:
                group_event = task2events[task]
                # Compact history is an effective-state projection:
                # - For regular action reruns (e.g. do-while), latest source_event_id
                #   wins for a given (action_ref, stream_id).
                # - For looped child workflows (loop_index != None), preserve fan-in
                #   aggregation behavior and keep a list of per-index results.
                if group_event.loop_index is not None or event.loop_index is not None:
                    group_event.child_wf_count += 1
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
                    result_list: list[Any]
                    if isinstance(group_event.action_result, list):
                        result_list = group_event.action_result
                    else:
                        result_list = [group_event.action_result]
                        group_event.action_result = result_list
                    result_list.append(event.action_result)
                elif event.source_event_id >= group_event.source_event_id:
                    task2events[task] = event
            else:
                task2events[task] = event
                # There's an edge case where a direct child wf invocation and a single looped child wf invocation
                # is ambiguous - how do we tell whether we should wrap the result in a list or not?
                # We use Temporal memo to store the loop index, so we can detect this case
                # If the loop index is None, it means it was a direct child wf invocation
                # Otherwise, it was a looped child wf invocation
                if event.loop_index is not None:
                    task2events[task].action_result = [event.action_result]

        compact_events = list(task2events.values())
        if include_pinned_synthetic:
            compact_events = await self._stitch_pinned_compact_events(
                wf_exec_id=wf_exec_id,
                compact_events=compact_events,
            )
        return compact_events

    async def get_external_action_result(
        self,
        wf_exec_id: WorkflowExecutionID,
        event_id: int,
    ) -> ExternalObject:
        """Get an ExternalObject result for an event/source event ID.

        The provided event_id may be:
        - the completed event ID (e.g. ACTIVITY_TASK_COMPLETED), or
        - the source scheduled event ID used by compact event payloads.
        """
        source_match, stored = await self._get_stored_result_for_event(
            wf_exec_id=wf_exec_id,
            event_id=event_id,
        )

        match stored:
            case ExternalObject() as external:
                return external
            case _:
                raise TypeError(
                    f"Event {source_match.event_id} result is not external (got {stored.type})"
                )

    async def get_collection_action_result(
        self,
        wf_exec_id: WorkflowExecutionID,
        event_id: int,
    ) -> CollectionObject:
        """Get a CollectionObject result for an event/source event ID."""
        source_match, stored = await self._get_stored_result_for_event(
            wf_exec_id=wf_exec_id,
            event_id=event_id,
        )
        match stored:
            case CollectionObject() as collection:
                return collection
            case _:
                raise TypeError(
                    f"Event {source_match.event_id} result is not a collection (got {stored.type})"
                )

    async def get_collection_page(
        self,
        wf_exec_id: WorkflowExecutionID,
        event_id: int,
        *,
        offset: int,
        limit: int,
    ) -> tuple[CollectionObject, list[Any]]:
        """Get a page from a collection result without full materialization."""
        collection = await self.get_collection_action_result(wf_exec_id, event_id)
        items = await get_storage_collection_page(
            collection, offset=offset, limit=limit
        )
        return collection, items

    async def get_collection_item_for_object_ops(
        self,
        wf_exec_id: WorkflowExecutionID,
        event_id: int,
        *,
        index: int,
    ) -> StoredObject | Any:
        """Resolve a collection item for object preview/download operations."""
        if index < 0:
            raise ValueError(f"collection index must be >= 0, got {index}")

        collection, items = await self.get_collection_page(
            wf_exec_id=wf_exec_id,
            event_id=event_id,
            offset=index,
            limit=1,
        )
        if not items:
            raise IndexError(
                f"Collection index {index} out of range [0, {collection.count})"
            )

        item = items[0]
        if collection.element_kind == "stored_object":
            try:
                return StoredObjectValidator.validate_python(item)
            except ValidationError as e:
                raise TypeError(
                    f"Collection item at index {index} is not a valid StoredObject"
                ) from e
        return item

    async def _resolve_completed_event(
        self,
        wf_exec_id: WorkflowExecutionID,
        event_id: int,
    ) -> HistoryEvent:
        """Resolve a completed Temporal event by event ID or source event ID."""
        handle = self.handle(wf_exec_id)
        source_match: HistoryEvent | None = None

        async for event in handle.fetch_history_events():
            if not is_close_event(event):
                continue
            if event.event_id == event_id:
                source_match = event
                break
            if get_source_event_id(event) == event_id:
                source_match = event

        if source_match is None:
            raise WorkflowExecutionResultNotFoundError(
                f"No completed event found for event_id={event_id}"
            )
        return source_match

    async def _get_stored_result_for_event(
        self,
        *,
        wf_exec_id: WorkflowExecutionID,
        event_id: int,
    ) -> tuple[HistoryEvent, StoredObject]:
        """Get a StoredObject from a completed event or its source event ID."""
        source_match = await self._resolve_completed_event(
            wf_exec_id=wf_exec_id,
            event_id=event_id,
        )
        stored = await get_stored_result(source_match)
        if stored is None:
            raise TypeError(
                f"Event {source_match.event_id} result is not a StoredObject"
            )
        return source_match, stored

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
                    group = await EventGroup.from_initiated_child_workflow(event)
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
                    result = await extract_first(
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
                    run_args_data = await extract_first(attrs.input)
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
                    result = await extract_first(
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
                    if not (group := await EventGroup.from_scheduled_activity(event)):
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
                    result = await extract_first(
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
                    data = await extract_first(attrs.input)
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
                    group = await EventGroup.from_accepted_workflow_update(event)
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
                        result = await extract_first(outcome.success)
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
        time_anchor: datetime.datetime | None = None,
        registry_lock: RegistryLock | None = None,
        memo: dict[str, Any] | None = None,
    ) -> WorkflowExecutionCreateResponse:
        """Create a new workflow execution.

        Note: This method schedules the workflow execution and returns immediately.

        Args:
            memo: Optional memo dict to store with the workflow execution.
                  Useful for correlation (e.g., parent_wf_exec_id).
        """
        wf_exec_id = generate_exec_id(wf_id)
        coro = self._start_workflow(
            dsl=dsl,
            wf_id=wf_id,
            trigger_type=trigger_type,
            wf_exec_id=wf_exec_id,
            time_anchor=time_anchor,
            registry_lock=registry_lock,
            memo=memo,
            trigger_inputs=payload,
            execution_type=ExecutionType.PUBLISHED,
        )
        task = asyncio.ensure_future(coro)
        task.add_done_callback(self._handle_background_task_exception)
        return WorkflowExecutionCreateResponse(
            message="Workflow execution started",
            wf_id=wf_id,
            wf_exec_id=wf_exec_id,
        )

    @audit_log(resource_type="workflow_execution", action="create")
    async def create_workflow_execution_wait_for_start(
        self,
        dsl: DSLInput,
        *,
        wf_id: WorkflowID,
        payload: TriggerInputs | None = None,
        trigger_type: TriggerType = TriggerType.MANUAL,
        wf_exec_id: WorkflowExecutionID | None = None,
        time_anchor: datetime.datetime | None = None,
        registry_lock: RegistryLock | None = None,
        memo: dict[str, Any] | None = None,
    ) -> WorkflowExecutionCreateResponse:
        """Create a workflow execution and wait until Temporal acknowledges start."""
        if wf_exec_id is None:
            wf_exec_id = generate_exec_id(wf_id)

        await self._start_workflow(
            dsl=dsl,
            wf_id=wf_id,
            wf_exec_id=wf_exec_id,
            trigger_inputs=payload,
            trigger_type=trigger_type,
            execution_type=ExecutionType.PUBLISHED,
            time_anchor=time_anchor,
            registry_lock=registry_lock,
            memo=memo,
        )

        return WorkflowExecutionCreateResponse(
            message="Workflow execution started",
            wf_id=wf_id,
            wf_exec_id=wf_exec_id,
        )

    def create_draft_workflow_execution_nowait(
        self,
        dsl: DSLInput,
        *,
        wf_id: WorkflowID,
        payload: TriggerInputs | None = None,
        trigger_type: TriggerType = TriggerType.MANUAL,
        time_anchor: datetime.datetime | None = None,
        registry_lock: RegistryLock | None = None,
        pinned_action_results: dict[str, TaskResult] | None = None,
        pinned_source_execution_id: WorkflowExecutionID | None = None,
    ) -> WorkflowExecutionCreateResponse:
        """Create a new draft workflow execution.

        Draft executions use the draft workflow graph and resolve aliases from draft workflows.
        This method schedules the workflow execution and returns immediately.
        """
        wf_exec_id = generate_exec_id(wf_id)
        coro = self._start_workflow(
            dsl=dsl,
            wf_id=wf_id,
            trigger_type=trigger_type,
            wf_exec_id=wf_exec_id,
            time_anchor=time_anchor,
            registry_lock=registry_lock,
            trigger_inputs=payload,
            execution_type=ExecutionType.DRAFT,
            pinned_action_results=pinned_action_results,
            pinned_source_execution_id=pinned_source_execution_id,
        )
        task = asyncio.ensure_future(coro)
        task.add_done_callback(self._handle_background_task_exception)
        return WorkflowExecutionCreateResponse(
            message="Draft workflow execution started",
            wf_id=wf_id,
            wf_exec_id=wf_exec_id,
        )

    @audit_log(resource_type="workflow_execution", action="create")
    async def create_draft_workflow_execution_wait_for_start(
        self,
        dsl: DSLInput,
        *,
        wf_id: WorkflowID,
        payload: TriggerInputs | None = None,
        trigger_type: TriggerType = TriggerType.MANUAL,
        wf_exec_id: WorkflowExecutionID | None = None,
        time_anchor: datetime.datetime | None = None,
        registry_lock: RegistryLock | None = None,
        pinned_action_results: dict[str, TaskResult] | None = None,
        pinned_source_execution_id: WorkflowExecutionID | None = None,
    ) -> WorkflowExecutionCreateResponse:
        """Create a draft workflow execution and wait until Temporal acknowledges start."""
        if wf_exec_id is None:
            wf_exec_id = generate_exec_id(wf_id)

        await self._start_workflow(
            dsl=dsl,
            wf_id=wf_id,
            wf_exec_id=wf_exec_id,
            trigger_inputs=payload,
            trigger_type=trigger_type,
            execution_type=ExecutionType.DRAFT,
            time_anchor=time_anchor,
            registry_lock=registry_lock,
            pinned_action_results=pinned_action_results,
            pinned_source_execution_id=pinned_source_execution_id,
        )

        return WorkflowExecutionCreateResponse(
            message="Draft workflow execution started",
            wf_id=wf_id,
            wf_exec_id=wf_exec_id,
        )

    @audit_log(resource_type="workflow_execution", action="create")
    async def create_draft_workflow_execution(
        self,
        dsl: DSLInput,
        *,
        wf_id: WorkflowID,
        payload: TriggerInputs | None = None,
        trigger_type: TriggerType = TriggerType.MANUAL,
        wf_exec_id: WorkflowExecutionID | None = None,
        time_anchor: datetime.datetime | None = None,
        registry_lock: RegistryLock | None = None,
        pinned_action_results: dict[str, TaskResult] | None = None,
        pinned_source_execution_id: WorkflowExecutionID | None = None,
    ) -> WorkflowDispatchResponse:
        """Create a new draft workflow execution.

        Note: This method blocks until the workflow execution completes.
        """
        if wf_exec_id is None:
            wf_exec_id = generate_exec_id(wf_id)

        return await self._dispatch_workflow(
            dsl=dsl,
            wf_id=wf_id,
            wf_exec_id=wf_exec_id,
            trigger_inputs=payload,
            trigger_type=trigger_type,
            execution_type=ExecutionType.DRAFT,
            time_anchor=time_anchor,
            registry_lock=registry_lock,
            pinned_action_results=pinned_action_results,
            pinned_source_execution_id=pinned_source_execution_id,
        )

    @audit_log(resource_type="workflow_execution", action="create")
    async def create_workflow_execution(
        self,
        dsl: DSLInput,
        *,
        wf_id: WorkflowID,
        payload: TriggerInputs | None = None,
        trigger_type: TriggerType = TriggerType.MANUAL,
        wf_exec_id: WorkflowExecutionID | None = None,
        time_anchor: datetime.datetime | None = None,
        registry_lock: RegistryLock | None = None,
        memo: dict[str, Any] | None = None,
    ) -> WorkflowDispatchResponse:
        """Create a new workflow execution.

        Note: This method blocks until the workflow execution completes.

        Args:
            memo: Optional memo dict to store with the workflow execution.
                  Useful for correlation (e.g., parent_wf_exec_id).
        """
        if wf_exec_id is None:
            wf_exec_id = generate_exec_id(wf_id)

        return await self._dispatch_workflow(
            dsl=dsl,
            wf_id=wf_id,
            wf_exec_id=wf_exec_id,
            trigger_inputs=payload,
            trigger_type=trigger_type,
            time_anchor=time_anchor,
            registry_lock=registry_lock,
            memo=memo,
        )

    async def _dispatch_workflow(
        self,
        dsl: DSLInput,
        wf_id: WorkflowID,
        wf_exec_id: WorkflowExecutionID,
        trigger_inputs: TriggerInputs | None = None,
        trigger_type: TriggerType = TriggerType.MANUAL,
        execution_type: ExecutionType = ExecutionType.PUBLISHED,
        time_anchor: datetime.datetime | None = None,
        registry_lock: RegistryLock | None = None,
        pinned_action_results: dict[str, TaskResult] | None = None,
        pinned_source_execution_id: WorkflowExecutionID | None = None,
        **kwargs: Any,
    ) -> WorkflowDispatchResponse:
        if self.role is None:
            raise ValueError("Role is required to dispatch a workflow")
        if rpc_timeout := config.TEMPORAL__CLIENT_RPC_TIMEOUT:
            kwargs["rpc_timeout"] = datetime.timedelta(seconds=float(rpc_timeout))
        if task_timeout := config.TEMPORAL__TASK_TIMEOUT:
            kwargs.setdefault(
                "task_timeout", datetime.timedelta(seconds=float(task_timeout))
            )
        # Resolve execution timeout based on workspace settings
        if execution_timeout := await self._resolve_execution_timeout(
            seconds=dsl.config.timeout
        ):
            kwargs["execution_timeout"] = execution_timeout

        # Mint time_anchor for webhook/manual triggers if not explicitly provided.
        # This ensures the time_anchor is baked into workflow input and survives resets.
        # Scheduled workflows resolve time_anchor via local activity using TemporalScheduledStartTime.
        if time_anchor is None and trigger_type in (
            TriggerType.WEBHOOK,
            TriggerType.MANUAL,
            TriggerType.CASE,
        ):
            time_anchor = datetime.datetime.now(datetime.UTC)

        # Storing the trigger inputs as a StoredObject
        trigger_inputs_ref: StoredObject | None = None
        if trigger_inputs is not None:
            storage = get_object_storage()
            trigger_inputs_ref = await storage.store(
                f"{wf_exec_id}/trigger.json", trigger_inputs
            )

        logger.info(
            f"Executing DSL workflow: {dsl.title}",
            role=self.role,
            wf_exec_id=wf_exec_id,
            run_config=dsl.config,
            kwargs=kwargs,
            trigger_type=trigger_type,
            execution_type=execution_type,
            registry_lock=registry_lock,
            stored_type=trigger_inputs_ref.type if trigger_inputs_ref else "<none>",
        )

        pairs = [trigger_type.to_temporal_search_attr_pair()]
        if self.role is not None:
            if self.role.user_id is not None:
                pairs.append(
                    TemporalSearchAttr.TRIGGERED_BY_USER_ID.create_pair(
                        str(self.role.user_id)
                    )
                )
            if self.role.workspace_id is not None:
                pairs.append(
                    TemporalSearchAttr.WORKSPACE_ID.create_pair(
                        str(self.role.workspace_id)
                    )
                )
        # Add execution type search attribute
        pairs.append(execution_type.to_temporal_search_attr_pair())
        search_attrs = TypedSearchAttributes(search_attributes=pairs)
        try:
            result = await self._client.execute_workflow(
                DSLWorkflow.run,
                DSLRunArgs(
                    dsl=dsl,
                    role=self.role,
                    wf_id=wf_id,
                    trigger_inputs=trigger_inputs_ref,
                    execution_type=execution_type,
                    time_anchor=time_anchor,
                    registry_lock=registry_lock,
                    pinned_action_results=pinned_action_results or {},
                    pinned_source_execution_id=pinned_source_execution_id,
                ),
                id=wf_exec_id,
                task_queue=config.TEMPORAL__CLUSTER_QUEUE,
                retry_policy=RETRY_POLICIES["workflow:fail_fast"],
                search_attributes=search_attrs,
                **kwargs,
            )
        except WorkflowFailureError as e:
            if isinstance(e.cause, TerminatedError):
                self.logger.info(
                    "Workflow execution terminated by user",
                    role=self.role,
                    wf_exec_id=wf_exec_id,
                    cause=e.cause,
                )
                # Don't re-raise for expected terminations
                return WorkflowDispatchResponse(
                    wf_id=wf_id,
                    result={
                        "status": "terminated",
                        "message": "Workflow execution terminated by user",
                    },
                )
            else:
                cause_message = self.format_failure_cause(e.cause)
                self.logger.error(
                    "Workflow execution failed",
                    role=self.role,
                    wf_exec_id=wf_exec_id,
                    cause=e.cause,
                    cause_message=cause_message,
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

    async def _start_workflow(
        self,
        dsl: DSLInput,
        *,
        wf_id: WorkflowID,
        wf_exec_id: WorkflowExecutionID,
        trigger_inputs: TriggerInputs | None = None,
        trigger_type: TriggerType = TriggerType.MANUAL,
        execution_type: ExecutionType = ExecutionType.PUBLISHED,
        time_anchor: datetime.datetime | None = None,
        registry_lock: RegistryLock | None = None,
        pinned_action_results: dict[str, TaskResult] | None = None,
        pinned_source_execution_id: WorkflowExecutionID | None = None,
        **kwargs: Any,
    ) -> None:
        if self.role is None:
            raise ValueError("Role is required to dispatch a workflow")
        if rpc_timeout := config.TEMPORAL__CLIENT_RPC_TIMEOUT:
            kwargs["rpc_timeout"] = datetime.timedelta(seconds=float(rpc_timeout))
        if task_timeout := config.TEMPORAL__TASK_TIMEOUT:
            kwargs.setdefault(
                "task_timeout", datetime.timedelta(seconds=float(task_timeout))
            )
        if execution_timeout := await self._resolve_execution_timeout(
            seconds=dsl.config.timeout
        ):
            kwargs["execution_timeout"] = execution_timeout

        if time_anchor is None and trigger_type in (
            TriggerType.WEBHOOK,
            TriggerType.MANUAL,
            TriggerType.CASE,
        ):
            time_anchor = datetime.datetime.now(datetime.UTC)

        trigger_inputs_ref: StoredObject | None = None
        if trigger_inputs is not None:
            storage = get_object_storage()
            trigger_inputs_ref = await storage.store(
                f"{wf_exec_id}/trigger.json", trigger_inputs
            )

        try:
            dispatch_timeout_seconds = float(config.TEMPORAL__CLIENT_RPC_TIMEOUT)
        except (TypeError, ValueError):
            dispatch_timeout_seconds = 900.0
            self.logger.warning(
                "Invalid TEMPORAL__CLIENT_RPC_TIMEOUT value, using default",
                value=config.TEMPORAL__CLIENT_RPC_TIMEOUT,
                default=dispatch_timeout_seconds,
            )
        if dispatch_timeout_seconds <= 0:
            dispatch_timeout_seconds = 900.0
        # Keep start dispatch bounded so callers don't block indefinitely when
        # Temporal is down or the worker is unreachable.
        start_timeout = min(dispatch_timeout_seconds, 30.0)

        logger.info(
            f"Starting DSL workflow: {dsl.title}",
            role=self.role,
            wf_exec_id=wf_exec_id,
            run_config=dsl.config,
            kwargs=kwargs,
            trigger_type=trigger_type,
            execution_type=execution_type,
            registry_lock=registry_lock,
            stored_type=trigger_inputs_ref.type if trigger_inputs_ref else "<none>",
            task_queue=config.TEMPORAL__CLUSTER_QUEUE,
            workflow_id=wf_id,
        )

        pairs = [trigger_type.to_temporal_search_attr_pair()]
        if self.role is not None:
            if self.role.user_id is not None:
                pairs.append(
                    TemporalSearchAttr.TRIGGERED_BY_USER_ID.create_pair(
                        str(self.role.user_id)
                    )
                )
            if self.role.workspace_id is not None:
                pairs.append(
                    TemporalSearchAttr.WORKSPACE_ID.create_pair(
                        str(self.role.workspace_id)
                    )
                )
        pairs.append(execution_type.to_temporal_search_attr_pair())
        search_attrs = TypedSearchAttributes(search_attributes=pairs)

        try:
            await asyncio.wait_for(
                self._client.start_workflow(
                    DSLWorkflow.run,
                    DSLRunArgs(
                        dsl=dsl,
                        role=self.role,
                        wf_id=wf_id,
                        trigger_inputs=trigger_inputs_ref,
                        execution_type=execution_type,
                        time_anchor=time_anchor,
                        registry_lock=registry_lock,
                        pinned_action_results=pinned_action_results or {},
                        pinned_source_execution_id=pinned_source_execution_id,
                    ),
                    id=wf_exec_id,
                    task_queue=config.TEMPORAL__CLUSTER_QUEUE,
                    retry_policy=RETRY_POLICIES["workflow:fail_fast"],
                    search_attributes=search_attrs,
                    **kwargs,
                ),
                timeout=start_timeout,
            )
        except TimeoutError as e:
            self.logger.error(
                "Timed out while dispatching workflow start",
                wf_exec_id=wf_exec_id,
                task_queue=config.TEMPORAL__CLUSTER_QUEUE,
                timeout_seconds=start_timeout,
                error=str(e),
            )
            raise TimeoutError(
                "Timed out while dispatching workflow to Temporal. "
                "Verify Temporal reachability, namespace, and worker availability."
            ) from e
        except RPCError as e:
            self.logger.error(
                f"Temporal service RPC error occurred while starting the workflow: {e}",
                role=self.role,
                wf_exec_id=wf_exec_id,
                e=e,
            )
            raise e

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
