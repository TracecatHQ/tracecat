from __future__ import annotations

import asyncio
import base64
import datetime
import hashlib
import json
import uuid
from collections import OrderedDict
from collections.abc import AsyncGenerator, Sequence
from dataclasses import dataclass
from typing import Any, cast

import orjson
import temporalio.api.enums.v1
from pydantic import BaseModel, ValidationError
from temporalio.api.common.v1 import message_pb2
from temporalio.api.enums.v1 import EventType, PendingActivityState, ResetReapplyType
from temporalio.api.history.v1 import HistoryEvent
from temporalio.api.workflowservice.v1 import request_response_pb2
from temporalio.client import (
    Client,
    WorkflowExecution,
    WorkflowFailureError,
    WorkflowHandle,
    WorkflowHistoryEventFilterType,
)
from temporalio.common import TypedSearchAttributes, WorkflowIDReusePolicy
from temporalio.exceptions import TerminatedError
from temporalio.service import RPCError

from tracecat import config
from tracecat.audit.logger import audit_log
from tracecat.auth.types import Role
from tracecat.contexts import ctx_role
from tracecat.db.models import Interaction
from tracecat.dsl.client import get_temporal_client
from tracecat.dsl.common import RETRY_POLICIES, DSLInput, DSLRunArgs
from tracecat.dsl.enums import PlatformAction
from tracecat.dsl.schemas import TriggerInputs
from tracecat.dsl.types import Task
from tracecat.dsl.workflow import DSLWorkflow
from tracecat.ee.interactions.schemas import InteractionInput
from tracecat.ee.interactions.service import InteractionService
from tracecat.identifiers import UserID, WorkspaceID
from tracecat.identifiers.workflow import (
    WorkflowExecutionID,
    WorkflowID,
    generate_exec_id,
)
from tracecat.logger import logger
from tracecat.pagination import CursorPaginationParams
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
    retrieve_stored_object,
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
    is_unreadable_temporal_payload,
)
from tracecat.workflow.executions.constants import (
    WF_COMPLETED_REF,
    WF_FAILURE_REF,
    WF_TRIGGER_REF,
)
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
    WorkflowExecutionBulkResetItemResult,
    WorkflowExecutionCreateResponse,
    WorkflowExecutionEvent,
    WorkflowExecutionEventCompact,
    WorkflowExecutionRelationFilter,
    WorkflowExecutionResetPointRead,
    WorkflowExecutionResetReapplyType,
    WorkflowExecutionStatusFilterMode,
    WorkflowExecutionStatusLiteral,
)
from tracecat.workspaces.service import WorkspaceService


class WorkflowExecutionResultNotFoundError(ValueError):
    """Raised when no matching completed event exists for a given event ID."""


class WorkflowExecutionResultMaskedError(PermissionError):
    """Raised when a masked action result is requested through object APIs."""


class WorkflowExecutionNotFoundError(ValueError):
    """Raised when a workflow execution is not visible in the current workspace."""


@dataclass(frozen=True)
class WorkflowExecutionResolvedEvent:
    event: HistoryEvent
    should_mask_output: bool


@dataclass(frozen=True)
class WorkflowExecutionStoredResult:
    event: HistoryEvent
    stored: StoredObject
    should_mask_output: bool


@dataclass(frozen=True)
class WorkflowExecutionsPage:
    items: list[WorkflowExecution]
    next_cursor: str | None
    prev_cursor: str | None
    has_more: bool
    has_previous: bool


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


REDACTED_ACTION_RESULT = "[REDACTED]"


def _redact_leaf_values(value: Any) -> Any:
    """Preserve result structure while redacting displayable values."""
    if isinstance(value, BaseModel):
        return _redact_leaf_values(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {key: _redact_leaf_values(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_redact_leaf_values(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_leaf_values(item) for item in value)
    return REDACTED_ACTION_RESULT


def _sanitize_action_result(mask_output: bool, action_result: Any) -> Any:
    """Redact action output for API consumers when the statement opts in."""
    if mask_output:
        return _redact_leaf_values(action_result)
    return action_result


async def _resolve_trigger_context(trigger_inputs: StoredObject | None) -> Any | None:
    """Materialize stored trigger inputs for compact history views."""
    if trigger_inputs is None:
        return None
    return await retrieve_stored_object(trigger_inputs)


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
        workspace_id = self.workspace_id
        workspace_clause = f"{TemporalSearchAttr.WORKSPACE_ID.value} = '{workspace_id}'"
        query = f"({query}) AND {workspace_clause}" if query else workspace_clause

        executions = []
        # NOTE: We operate under the assumption that `list_workflows` is ordered by StartTime
        # This appears to be true based on observation
        async for execution in self._client.list_workflows(query=query, **kwargs):
            executions.append(execution)
            if limit and len(executions) >= limit:
                break
        return executions

    @staticmethod
    def _build_query_fingerprint(
        *, query: str | None, relation: WorkflowExecutionRelationFilter
    ) -> str:
        payload = {
            "query": query or "",
            "relation": relation.value,
        }
        canonical = orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)
        return hashlib.sha256(canonical).hexdigest()

    @staticmethod
    def _encode_query_cursor(
        page_token: bytes | None,
        fingerprint: str,
        *,
        history: Sequence[bytes | None] | None = None,
    ) -> str:
        payload = {
            "token": (
                base64.urlsafe_b64encode(page_token).decode("ascii")
                if page_token is not None
                else None
            ),
            "fingerprint": fingerprint,
            "history": [
                (
                    base64.urlsafe_b64encode(token).decode("ascii")
                    if token is not None
                    else None
                )
                for token in (history or [])
            ],
        }
        encoded = base64.urlsafe_b64encode(
            orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)
        )
        return encoded.decode("ascii")

    @staticmethod
    def _decode_query_cursor(
        cursor: str,
    ) -> tuple[bytes | None, str, list[bytes | None]]:
        try:
            decoded = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
            data = json.loads(decoded)
            token_text = data["token"]
            fingerprint = data["fingerprint"]
            history_text = data.get("history", [])
            if token_text is not None and not isinstance(token_text, str):
                raise ValueError("Malformed workflow executions cursor")
            if not isinstance(fingerprint, str) or not isinstance(history_text, list):
                raise ValueError("Malformed workflow executions cursor")
            history: list[bytes | None] = []
            for entry in history_text:
                if entry is None:
                    history.append(None)
                elif isinstance(entry, str):
                    history.append(base64.urlsafe_b64decode(entry.encode("ascii")))
                else:
                    raise ValueError("Malformed workflow executions cursor")
            token = (
                base64.urlsafe_b64decode(token_text.encode("ascii"))
                if token_text is not None
                else None
            )
            return token, fingerprint, history
        except Exception as e:
            raise ValueError("Invalid workflow executions cursor") from e

    @staticmethod
    def _build_workflow_id_clause(
        workflow_id: WorkflowID,
        *,
        include_legacy: bool = True,
    ) -> str:
        short_id = workflow_id.short()
        wf_id_query = f"WorkflowId STARTS_WITH '{short_id}'"
        if include_legacy:
            legacy_wf_id = workflow_id.to_legacy()
            wf_id_query += f" OR WorkflowId STARTS_WITH '{legacy_wf_id}'"
        return f"({wf_id_query})"

    @staticmethod
    def _build_workflow_ids_clause(
        workflow_ids: Sequence[WorkflowID],
    ) -> str | None:
        if not workflow_ids:
            return None
        unique_ids = {wf_id.short(): wf_id for wf_id in workflow_ids}
        clauses = [
            WorkflowExecutionsService._build_workflow_id_clause(unique_ids[key])
            for key in sorted(unique_ids)
        ]
        if not clauses:
            return None
        return f"({' OR '.join(clauses)})"

    async def list_executions_paginated(
        self,
        *,
        pagination: CursorPaginationParams,
        workflow_id: WorkflowID | None = None,
        workflow_ids: Sequence[WorkflowID] | None = None,
        trigger_types: set[TriggerType] | None = None,
        triggered_by_user_id: UserID | None = None,
        statuses: set[WorkflowExecutionStatusLiteral] | None = None,
        status_mode: WorkflowExecutionStatusFilterMode = (
            WorkflowExecutionStatusFilterMode.INCLUDE
        ),
        execution_types: set[ExecutionType] | None = None,
        exclude_workflow_types: set[str] | None = None,
        start_time_from: datetime.datetime | None = None,
        start_time_to: datetime.datetime | None = None,
        close_time_from: datetime.datetime | None = None,
        close_time_to: datetime.datetime | None = None,
        duration_gte_seconds: int | None = None,
        duration_lte_seconds: int | None = None,
        relation: WorkflowExecutionRelationFilter = WorkflowExecutionRelationFilter.ALL,
    ) -> WorkflowExecutionsPage:
        query = build_query(
            workflow_id=workflow_id,
            trigger_types=trigger_types,
            triggered_by_user_id=triggered_by_user_id,
            statuses=statuses,
            status_mode=status_mode.value,
            execution_types=execution_types,
            exclude_workflow_types=exclude_workflow_types,
            start_time_from=start_time_from,
            start_time_to=start_time_to,
            close_time_from=close_time_from,
            close_time_to=close_time_to,
            duration_gte_seconds=duration_gte_seconds,
            duration_lte_seconds=duration_lte_seconds,
            workspace_id=self.workspace_id,
        )
        if workflow_ids_clause := self._build_workflow_ids_clause(workflow_ids or []):
            query = (
                f"{query} AND {workflow_ids_clause}" if query else workflow_ids_clause
            )

        fingerprint = self._build_query_fingerprint(query=query, relation=relation)
        next_page_token: bytes | None = None
        history: list[bytes | None] = []
        if pagination.cursor is not None:
            next_page_token, cursor_fingerprint, history = self._decode_query_cursor(
                pagination.cursor
            )
            if cursor_fingerprint != fingerprint:
                raise ValueError(
                    "Cursor no longer matches current filters. Retry without cursor."
                )

        iterator = self._client.list_workflows(
            query=query or None,
            page_size=pagination.limit,
            next_page_token=next_page_token,
        )
        await iterator.fetch_next_page(page_size=pagination.limit)
        executions = list(iterator.current_page or [])
        if relation == WorkflowExecutionRelationFilter.ROOT:
            executions = [
                execution for execution in executions if execution.parent_id is None
            ]
        elif relation == WorkflowExecutionRelationFilter.CHILD:
            executions = [
                execution for execution in executions if execution.parent_id is not None
            ]

        prev_cursor = None
        next_cursor = None
        if iterator.next_page_token:
            next_cursor = self._encode_query_cursor(
                iterator.next_page_token,
                fingerprint,
                history=[*history, next_page_token],
            )
        if history:
            prev_cursor = self._encode_query_cursor(
                history[-1],
                fingerprint,
                history=history[:-1],
            )
        return WorkflowExecutionsPage(
            items=executions,
            next_cursor=next_cursor,
            prev_cursor=prev_cursor,
            has_more=next_cursor is not None,
            has_previous=prev_cursor is not None,
        )

    def _role_workspace_id(self) -> str | None:
        if self.role is None or self.role.workspace_id is None:
            return None
        return str(self.role.workspace_id)

    @property
    def workspace_id(self) -> WorkspaceID:
        if self.role is None:
            raise ValueError("Role is required to query workflow executions")
        workspace_id = self.role.workspace_id
        if workspace_id is None:
            raise ValueError("Workspace ID is required to query workflow executions")
        if not isinstance(workspace_id, WorkspaceID):
            raise TypeError("Workspace ID must be a WorkspaceID")
        return workspace_id

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

    async def require_execution(
        self, wf_exec_id: WorkflowExecutionID
    ) -> WorkflowExecution:
        execution = await self.get_execution(wf_exec_id)
        if execution is None:
            raise WorkflowExecutionNotFoundError(
                f"Workflow execution not found: {wf_exec_id}"
            )
        return execution

    async def list_executions(
        self,
        workflow_id: WorkflowID | None = None,
        trigger_types: set[TriggerType] | None = None,
        triggered_by_user_id: UserID | None = None,
        exclude_workflow_types: set[str] | None = None,
        limit: int | None = None,
    ) -> list[WorkflowExecution]:
        """List all workflow executions."""
        query = build_query(
            workflow_id=workflow_id,
            trigger_types=trigger_types,
            triggered_by_user_id=triggered_by_user_id,
            exclude_workflow_types=exclude_workflow_types,
            workspace_id=self.workspace_id,
        )
        return await self.query_executions(query=query, limit=limit)

    async def list_executions_by_workflow_id(
        self, wf_id: WorkflowID
    ) -> list[WorkflowExecution]:
        """List all workflow executions by workflow ID."""
        query = build_query(workflow_id=wf_id, workspace_id=self.workspace_id)
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
        await self.require_execution(wf_exec_id)
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
            elif event.event_type is EventType.EVENT_TYPE_WORKFLOW_EXECUTION_STARTED:
                attrs = event.workflow_execution_started_event_attributes
                run_args_data = await extract_first(attrs.input)
                action_input = run_args_data
                if not is_unreadable_temporal_payload(run_args_data):
                    dsl_run_args = DSLRunArgs(**run_args_data)
                    action_input = await _resolve_trigger_context(
                        dsl_run_args.trigger_inputs
                    )
                event_time = event.event_time.ToDatetime(datetime.UTC)
                id2event[event.event_id] = WorkflowExecutionEventCompact(
                    source_event_id=event.event_id,
                    schedule_time=event_time,
                    start_time=event_time,
                    close_time=event_time,
                    curr_event_type=WorkflowEventType.WORKFLOW_EXECUTION_STARTED,
                    status=WorkflowExecutionEventStatus.COMPLETED,
                    action_name=WF_TRIGGER_REF,
                    action_ref=WF_TRIGGER_REF,
                    action_input=action_input,
                )
            # ── synthetic compact event for top-level workflow failure ──
            elif event.event_type is EventType.EVENT_TYPE_WORKFLOW_EXECUTION_FAILED:
                failure = await EventFailure.from_history_event(event)
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
                    raw_result = await get_result(event)
                    source.action_result = _sanitize_action_result(
                        source.should_mask_output, raw_result
                    )
                    (
                        source.while_iteration,
                        source.while_continue,
                    ) = _extract_while_metadata(source.action_name, raw_result)
                if is_error_event(event):
                    source.action_error = await EventFailure.from_history_event(event)

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

        return list(task2events.values())

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
        stored_result = await self._get_stored_result_for_event(
            wf_exec_id=wf_exec_id,
            event_id=event_id,
        )
        self._raise_if_result_masked(stored_result)

        match stored_result.stored:
            case ExternalObject() as external:
                return external
            case _:
                raise TypeError(
                    f"Event {stored_result.event.event_id} result is not external "
                    f"(got {stored_result.stored.type})"
                )

    async def get_collection_action_result(
        self,
        wf_exec_id: WorkflowExecutionID,
        event_id: int,
    ) -> CollectionObject:
        """Get a CollectionObject result for an event/source event ID."""
        stored_result = await self._get_stored_result_for_event(
            wf_exec_id=wf_exec_id,
            event_id=event_id,
        )
        self._raise_if_result_masked(stored_result)

        match stored_result.stored:
            case CollectionObject() as collection:
                return collection
            case _:
                raise TypeError(
                    f"Event {stored_result.event.event_id} result is not a "
                    f"collection (got {stored_result.stored.type})"
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
        resolved = await self._resolve_completed_event_with_metadata(
            wf_exec_id,
            event_id,
        )
        return resolved.event

    async def _resolve_completed_event_with_metadata(
        self,
        wf_exec_id: WorkflowExecutionID,
        event_id: int,
    ) -> WorkflowExecutionResolvedEvent:
        """Resolve a completed Temporal event with source-event display metadata."""
        await self.require_execution(wf_exec_id)
        handle = self.handle(wf_exec_id)
        source_masks: dict[int, bool] = {}
        source_match: WorkflowExecutionResolvedEvent | None = None

        async for event in handle.fetch_history_events():
            if is_scheduled_event(event):
                try:
                    source = await WorkflowExecutionEventCompact.from_source_event(
                        event
                    )
                except Exception as e:
                    self.logger.warning(
                        "Failed to parse source event mask metadata; treating result as masked",
                        event_id=event.event_id,
                        error=e,
                    )
                    source_masks[event.event_id] = True
                else:
                    if source is None:
                        continue
                    source_masks[event.event_id] = source.should_mask_output

            if not is_close_event(event):
                continue
            source_event_id = get_source_event_id(event)
            should_mask_output = (
                source_masks.get(source_event_id, False)
                if source_event_id is not None
                else False
            )
            resolved = WorkflowExecutionResolvedEvent(
                event=event,
                should_mask_output=should_mask_output,
            )
            if event.event_id == event_id:
                return resolved
            if source_event_id == event_id:
                source_match = resolved

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
    ) -> WorkflowExecutionStoredResult:
        """Get a StoredObject from a completed event or its source event ID."""
        source_match = await self._resolve_completed_event_with_metadata(
            wf_exec_id=wf_exec_id,
            event_id=event_id,
        )
        stored = await get_stored_result(source_match.event)
        if stored is None:
            raise TypeError(
                f"Event {source_match.event.event_id} result is not a StoredObject"
            )
        return WorkflowExecutionStoredResult(
            event=source_match.event,
            stored=stored,
            should_mask_output=source_match.should_mask_output,
        )

    @staticmethod
    def _raise_if_result_masked(stored_result: WorkflowExecutionStoredResult) -> None:
        if stored_result.should_mask_output:
            raise WorkflowExecutionResultMaskedError(
                "Action result is masked by `mask_output` and cannot be retrieved "
                "through object APIs."
            )

    async def list_workflow_execution_events(
        self,
        wf_exec_id: WorkflowExecutionID,
        event_filter_type: WorkflowHistoryEventFilterType = WorkflowHistoryEventFilterType.ALL_EVENT,
        **kwargs,
    ) -> list[WorkflowExecutionEvent]:
        """List the event history of a workflow execution."""
        await self.require_execution(wf_exec_id)

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
                    role = (
                        group.action_input.role
                        if isinstance(group.action_input, DSLRunArgs)
                        else None
                    )
                    events.append(
                        WorkflowExecutionEvent(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(datetime.UTC),
                            event_type=WorkflowEventType.START_CHILD_WORKFLOW_EXECUTION_INITIATED,
                            event_group=group,
                            task_id=event.task_id,
                            role=role,
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
                    raw_result = await extract_first(
                        event.child_workflow_execution_completed_event_attributes.result
                    )
                    initiator_event_id = event.child_workflow_execution_completed_event_attributes.initiated_event_id
                    group = event_group_names.get(initiator_event_id)
                    result = _sanitize_action_result(
                        group.should_mask_output if group else False,
                        raw_result,
                    )
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
                            failure=await EventFailure.from_history_event(event),
                        )
                    )

                # === Workflow Execution Events ===
                case EventType.EVENT_TYPE_WORKFLOW_EXECUTION_STARTED:
                    attrs = event.workflow_execution_started_event_attributes
                    run_args_data = await extract_first(attrs.input)
                    dsl_run_args = (
                        None
                        if is_unreadable_temporal_payload(run_args_data)
                        else DSLRunArgs(**run_args_data)
                    )
                    # Empty strings coerce to None
                    parent_exec_id = attrs.parent_workflow_execution.workflow_id or None
                    events.append(
                        WorkflowExecutionEvent(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(datetime.UTC),
                            event_type=WorkflowEventType.WORKFLOW_EXECUTION_STARTED,
                            parent_wf_exec_id=parent_exec_id,
                            task_id=event.task_id,
                            role=dsl_run_args.role if dsl_run_args else None,
                            workflow_timeout=(
                                dsl_run_args.runtime_config.timeout
                                if dsl_run_args
                                else None
                            ),
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
                            failure=await EventFailure.from_history_event(event),
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
                    raw_result = await extract_first(
                        event.activity_task_completed_event_attributes.result
                    )
                    result = _sanitize_action_result(
                        group.should_mask_output,
                        raw_result,
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
                            failure=await EventFailure.from_history_event(event),
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
                    result = (
                        data
                        if is_unreadable_temporal_payload(data)
                        else InteractionInput(**data)
                    )
                    events.append(
                        WorkflowExecutionEvent(
                            event_id=event.event_id,
                            event_time=event.event_time.ToDatetime(datetime.UTC),
                            event_type=WorkflowEventType.WORKFLOW_EXECUTION_SIGNALED,
                            task_id=event.task_id,
                            result=result,
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
                                failure=await EventFailure.from_history_event(event),
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
        await self.require_execution(wf_exec_id)

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
                    result=InlineObject(
                        type="inline",
                        data={
                            "status": "terminated",
                            "message": "Workflow execution terminated by user",
                        },
                    ),
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
        # Workflow results can include rich objects (e.g., StoredObject variants)
        # that are not guaranteed to be JSON serializable.
        self.logger.debug("Workflow result", result=result)
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
                    ),
                    id=wf_exec_id,
                    task_queue=config.TEMPORAL__CLUSTER_QUEUE,
                    # Workflow execution IDs are immutable correlation keys.
                    # Retrying the same dispatch must not start a second run.
                    id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
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

    @staticmethod
    def _event_type_label(event_type: int) -> str:
        try:
            return EventType.Name(cast(EventType.ValueType, event_type)).removeprefix(
                "EVENT_TYPE_"
            )
        except ValueError:
            return f"UNKNOWN_{event_type}"

    @staticmethod
    def _is_resettable_event(event: HistoryEvent) -> bool:
        return event.event_type == EventType.EVENT_TYPE_WORKFLOW_TASK_COMPLETED

    @staticmethod
    def _to_temporal_reset_reapply_type(
        reapply_type: WorkflowExecutionResetReapplyType,
    ) -> ResetReapplyType.ValueType:
        match reapply_type:
            case WorkflowExecutionResetReapplyType.ALL_ELIGIBLE:
                return ResetReapplyType.RESET_REAPPLY_TYPE_ALL_ELIGIBLE
            case WorkflowExecutionResetReapplyType.SIGNAL_ONLY:
                return ResetReapplyType.RESET_REAPPLY_TYPE_SIGNAL
            case WorkflowExecutionResetReapplyType.NONE:
                return ResetReapplyType.RESET_REAPPLY_TYPE_NONE
            case _:
                raise ValueError(f"Unsupported reset reapply type: {reapply_type}")

    async def list_reset_points(
        self,
        wf_exec_id: WorkflowExecutionID,
        *,
        limit: int = 100,
    ) -> list[WorkflowExecutionResetPointRead]:
        await self.require_execution(wf_exec_id)
        points: list[WorkflowExecutionResetPointRead] = []
        first_resettable_event_id: int | None = None
        handle = self.handle(wf_exec_id)
        async for event in handle.fetch_history_events(
            event_filter_type=WorkflowHistoryEventFilterType.ALL_EVENT
        ):
            resettable = self._is_resettable_event(event)
            if resettable and first_resettable_event_id is None:
                first_resettable_event_id = event.event_id
            points.append(
                WorkflowExecutionResetPointRead(
                    event_id=event.event_id,
                    event_time=event.event_time.ToDatetime(datetime.UTC),
                    event_type=self._event_type_label(event.event_type),
                    label=f"Event {event.event_id}",
                    is_start=False,
                    is_resettable=resettable,
                )
            )
            if len(points) >= limit:
                break

        if first_resettable_event_id is not None:
            for point in points:
                point.is_start = point.event_id == first_resettable_event_id
        return points

    async def _resolve_reset_event_id(
        self,
        wf_exec_id: WorkflowExecutionID,
        *,
        event_id: int | None,
    ) -> int:
        reset_event_id: int | None = None
        first_resettable_event_id: int | None = None
        handle = self.handle(wf_exec_id)
        async for event in handle.fetch_history_events(
            event_filter_type=WorkflowHistoryEventFilterType.ALL_EVENT
        ):
            if not self._is_resettable_event(event):
                continue
            if first_resettable_event_id is None:
                first_resettable_event_id = event.event_id
            if event_id is None:
                reset_event_id = event.event_id
                break
            if event.event_id <= event_id:
                reset_event_id = event.event_id
            else:
                break

        if event_id is None and first_resettable_event_id is not None:
            return first_resettable_event_id
        if reset_event_id is None:
            if event_id is None:
                raise ValueError(
                    "No resettable point found for workflow execution start."
                )
            raise ValueError(
                f"No resettable point found at or before event {event_id}."
            )
        return reset_event_id

    async def reset_workflow_execution(
        self,
        wf_exec_id: WorkflowExecutionID,
        *,
        event_id: int | None,
        reason: str | None = None,
        reapply_type: WorkflowExecutionResetReapplyType = WorkflowExecutionResetReapplyType.ALL_ELIGIBLE,
    ) -> str:
        execution = await self.require_execution(wf_exec_id)
        workflow_task_finish_event_id = await self._resolve_reset_event_id(
            wf_exec_id, event_id=event_id
        )
        response = await self._client.workflow_service.reset_workflow_execution(
            request_response_pb2.ResetWorkflowExecutionRequest(
                namespace=config.TEMPORAL__CLUSTER_NAMESPACE,
                workflow_execution=message_pb2.WorkflowExecution(
                    workflow_id=execution.id,
                    run_id=execution.run_id,
                ),
                reason=reason or f"Reset workflow execution {wf_exec_id}",
                workflow_task_finish_event_id=workflow_task_finish_event_id,
                request_id=str(uuid.uuid4()),
                reset_reapply_type=self._to_temporal_reset_reapply_type(reapply_type),
                identity="tracecat-api",
            )
        )
        return response.run_id

    async def bulk_reset_workflow_executions(
        self,
        execution_ids: Sequence[WorkflowExecutionID],
        *,
        event_id: int | None,
        reason: str | None = None,
        reapply_type: WorkflowExecutionResetReapplyType = WorkflowExecutionResetReapplyType.ALL_ELIGIBLE,
        concurrency_limit: int = 10,
    ) -> list[WorkflowExecutionBulkResetItemResult]:
        semaphore = asyncio.Semaphore(max(1, concurrency_limit))

        async def _reset(
            execution_id: WorkflowExecutionID,
        ) -> WorkflowExecutionBulkResetItemResult:
            async with semaphore:
                try:
                    new_run_id = await self.reset_workflow_execution(
                        execution_id,
                        event_id=event_id,
                        reason=reason,
                        reapply_type=reapply_type,
                    )
                    return WorkflowExecutionBulkResetItemResult(
                        execution_id=execution_id,
                        ok=True,
                        new_run_id=new_run_id,
                    )
                except Exception as e:
                    return WorkflowExecutionBulkResetItemResult(
                        execution_id=execution_id,
                        ok=False,
                        error=str(e),
                    )

        return await asyncio.gather(
            *[_reset(execution_id) for execution_id in execution_ids]
        )

    async def cancel_workflow_execution(self, wf_exec_id: WorkflowExecutionID) -> None:
        """Cancel a workflow execution."""
        await self.require_execution(wf_exec_id)
        await self.handle(wf_exec_id).cancel()

    async def terminate_workflow_execution(
        self, wf_exec_id: WorkflowExecutionID, reason: str | None = None
    ) -> None:
        """Terminate a workflow execution."""
        await self.require_execution(wf_exec_id)
        await self.handle(wf_exec_id).terminate(reason=reason)
