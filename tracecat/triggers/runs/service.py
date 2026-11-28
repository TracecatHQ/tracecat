"""A trigger's recent runs and stopped events, and the trigger behind a run.

Runs are read from Temporal by ``TracecatTriggerId``, which starts set only
once the namespace has the attribute (``trigger_link``). A schedule's runs are
read by Temporal's built-in ``TemporalScheduledById`` instead, which every run
a schedule starts carries. Stopped events are the trigger's ``event_outbox``
rows that hit the attempt cap.

Access: everything goes through ``TriggersService.get_trigger`` (workspace,
the kind's read scope and entitlement), plus the read scope of the runs
listed: ``workflow:read`` for workflow runs, ``agent:read`` for agent runs.
"""

from __future__ import annotations

import base64
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import and_, func, literal, select, tuple_
from temporalio.service import RPCError, RPCStatusCode

from tracecat.agent.backends.registry import get_agent_backends, get_workflow_type
from tracecat.authz.controls import check_scopes, has_scope
from tracecat.db.models import (
    AgentPreset,
    AgentSession,
    EventOutbox,
    Schedule,
    Workflow,
)
from tracecat.dsl.client import get_temporal_client
from tracecat.identifiers.schedules import LEGACY_SCHEDULE_ID_PREFIX, ScheduleUUID
from tracecat.identifiers.workflow import WorkflowExecutionID, exec_id_to_parts
from tracecat.pagination import (
    CursorPaginationParams,
    PaginationError,
    PaginationErrorCode,
)
from tracecat.sanitization import redact_sensitive_text
from tracecat.service import BaseWorkspaceService
from tracecat.triggers.enums import TriggerErrorCode, TriggerKind, TriggerTargetType
from tracecat.triggers.exceptions import TriggerError
from tracecat.triggers.runs.schemas import (
    TriggerRunOrigin,
    TriggerRunRead,
    TriggerRunsPage,
    TriggerStoppedEventRead,
    TriggerStoppedEventsPage,
)
from tracecat.triggers.schemas import (
    ObjectStorageTriggerConfigRead,
    ScheduleTriggerConfigRead,
)
from tracecat.triggers.service import TriggersService
from tracecat.workflow.executions.common import build_query
from tracecat.workflow.executions.correlation import (
    AGENT_SESSION_CORRELATION_KIND,
    build_agent_session_correlation_id,
)
from tracecat.workflow.executions.enums import (
    TEMPORAL_SCHEDULED_BY_ID,
    TemporalSearchAttr,
)
from tracecat.workflow.executions.schemas import WorkflowExecutionRelationFilter
from tracecat.workflow.executions.service import WorkflowExecutionsService

DSL_WORKFLOW_TYPE = "DSLWorkflow"
"""Workflow runs; every other type a trigger starts is an agent turn."""
STOPPED_ERROR_MAX_LENGTH = 1_000


def _stopped_error(last_error: str | None, event: dict[str, Any]) -> str | None:
    """A stopped row's error without credentials or the event's own values.

    The dispatcher already redacts credentials when it saves the error; the
    event's string values (the object key above all) are blanked here too.
    """
    if not last_error:
        return None
    event_values = [value for value in event.values() if isinstance(value, str)]
    redacted = redact_sensitive_text(last_error, sensitive_values=event_values)
    return redacted[:STOPPED_ERROR_MAX_LENGTH]


def agent_turn_workflow_types() -> set[str]:
    """The workflow types of agent turns: every installed backend's turn workflow."""
    return {get_workflow_type(backend) for backend in get_agent_backends().values()}


def _encode_stopped_cursor(
    trigger_id: uuid.UUID, received: datetime, occurrence_key: str
) -> str:
    raw = f"{trigger_id}|{received.isoformat()}|{occurrence_key}".encode()
    return base64.urlsafe_b64encode(raw).decode()


def _decode_stopped_cursor(cursor: str, trigger_id: uuid.UUID) -> tuple[datetime, str]:
    """The (received, occurrence key) a stopped events cursor points after.

    A cursor names the trigger it was issued for, so one from another
    trigger's list is refused rather than silently moving the boundary.

    Raises:
        PaginationError: If the cursor is not one this trigger's list returned.
    """
    try:
        issued_for, received, occurrence_key = (
            base64.urlsafe_b64decode(cursor.encode()).decode().split("|")
        )
        if uuid.UUID(issued_for) != trigger_id:
            raise ValueError("Cursor is for another trigger")
        return datetime.fromisoformat(received), occurrence_key
    except ValueError as e:
        raise PaginationError(
            "Invalid cursor", code=PaginationErrorCode.INVALID_CURSOR
        ) from e


def _session_id_of(correlation_id: str | None) -> uuid.UUID | None:
    """The agent session an ``agent-session:<id>`` correlation ID names."""
    if correlation_id is None:
        return None
    kind, _, value = correlation_id.partition(":")
    if kind != AGENT_SESSION_CORRELATION_KIND:
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


class TriggerRunsService(BaseWorkspaceService):
    """Read what a trigger started and what it could not."""

    service_name = "trigger_runs"

    async def list_runs(
        self, trigger_id: uuid.UUID, params: CursorPaginationParams
    ) -> TriggerRunsPage:
        """The runs the trigger started: open runs first, then by close time.

        Root runs only; a workflow run's child runs inherit the attribute and
        are left out. Workflow runs need ``workflow:read`` and agent runs
        ``agent:read``; with one of them the page holds only that kind. A
        schedule starts workflow runs only, so it needs ``workflow:read``.

        Raises:
            ScopeDeniedError: Without the trigger's read scopes, or without
                both ``workflow:read`` and ``agent:read`` (``workflow:read``
                for a schedule).
            TriggerError: ``not_found`` if the workspace has no such trigger.
            ValueError: If the cursor belongs to another query.
        """
        trigger = await TriggersService(self.session, self.role).get_trigger(trigger_id)
        schedule = (
            trigger.config
            if isinstance(trigger.config, ScheduleTriggerConfigRead)
            else None
        )
        if schedule is not None:
            # A schedule starts workflow runs only.
            check_scopes(self.role, "workflow:read")
            workflows, agents = True, False
        else:
            workflows = has_scope(self.role.scopes or frozenset(), "workflow:read")
            agents = has_scope(self.role.scopes or frozenset(), "agent:read")
            if not (workflows and agents):
                check_scopes(
                    self.role, "workflow:read", "agent:read", require_all=False
                )
        agent_types = agent_turn_workflow_types()
        # An allowlist per readable kind: other types a trigger run can lead
        # to (such as registry tool runs) are never listed.
        workflow_types = ({DSL_WORKFLOW_TYPE} if workflows else set()) | (
            agent_types if agents else set()
        )
        executions = await WorkflowExecutionsService.connect(
            role=self.role, session=self.session
        )
        try:
            if schedule is not None:
                # Temporal stamps every run a schedule starts with the
                # schedule's ID, so runs from before trigger links are found too.
                page = await executions.list_executions_paginated(
                    pagination=params,
                    scheduled_by=schedule.schedule_id,
                    workflow_types=workflow_types,
                    relation=WorkflowExecutionRelationFilter.ROOT,
                )
            else:
                page = await executions.list_executions_paginated(
                    pagination=params,
                    trigger_id=trigger.id,
                    workflow_types=workflow_types,
                    relation=WorkflowExecutionRelationFilter.ROOT,
                )
        except RPCError as e:
            # With a cursor it is a bad cursor. On a first page, the namespace
            # doesn't have TracecatTriggerId yet, so no run carries it; the
            # schedule attribute is built in, so there it is an error. Any
            # other failure is an error, not "no runs".
            if e.status != RPCStatusCode.INVALID_ARGUMENT:
                raise
            if params.cursor is not None:
                raise ValueError("Invalid cursor") from e
            if schedule is not None:
                raise
            return TriggerRunsPage(items=[])
        items: list[TriggerRunRead] = []
        for execution in page.items:
            status = execution.status.name if execution.status else "RUNNING"
            # The query already holds only readable kinds; checked again here
            # so the split never rests on the type filter alone.
            if execution.workflow_type == DSL_WORKFLOW_TYPE and workflows:
                try:
                    workflow_id, _ = exec_id_to_parts(execution.id)
                except ValueError:
                    continue
                items.append(
                    TriggerRunRead(
                        target_type=TriggerTargetType.WORKFLOW,
                        wf_exec_id=execution.id,
                        workflow_id=workflow_id,
                        status=status,
                        start_time=execution.start_time,
                        close_time=execution.close_time,
                    )
                )
                continue
            if not agents or execution.workflow_type not in agent_types:
                continue
            session_id = _session_id_of(
                execution.typed_search_attributes.get(
                    TemporalSearchAttr.CORRELATION_ID.key
                )
            )
            if session_id is None:
                continue
            items.append(
                TriggerRunRead(
                    target_type=TriggerTargetType.AGENT,
                    wf_exec_id=execution.id,
                    session_id=session_id,
                    status=status,
                    start_time=execution.start_time,
                    close_time=execution.close_time,
                )
            )
        items = await self._with_target_names(items)
        return TriggerRunsPage(
            items=items,
            next_cursor=page.next_cursor,
            prev_cursor=page.prev_cursor,
            has_more=page.has_more,
            has_previous=page.has_previous,
        )

    async def _with_target_names(
        self, items: list[TriggerRunRead]
    ) -> list[TriggerRunRead]:
        """Fill each run's target: one query for workflows, one for sessions.

        An agent run is named after its session's preset, and gets the
        preset's ID, which its link needs.
        """
        workflow_ids = {
            uuid.UUID(str(i.workflow_id)) for i in items if i.workflow_id is not None
        }
        session_ids = {i.session_id for i in items if i.session_id is not None}
        titles: dict[uuid.UUID, str] = {}
        presets: dict[uuid.UUID, uuid.UUID | None] = {}
        if workflow_ids:
            rows = await self.session.execute(
                select(Workflow.id, Workflow.title).where(
                    Workflow.workspace_id == self.workspace_id,
                    Workflow.id.in_(workflow_ids),
                )
            )
            titles.update(rows.tuples().all())
        if session_ids:
            # A trigger's session is titled after the trigger, so the agent
            # preset's name says more; the title stands in if it was deleted.
            rows = await self.session.execute(
                select(
                    AgentSession.id,
                    func.coalesce(AgentPreset.name, AgentSession.title),
                    # Null for a deleted preset, so the run renders unlinked.
                    AgentPreset.id,
                )
                .outerjoin(
                    AgentPreset,
                    and_(
                        AgentPreset.id == AgentSession.agent_preset_id,
                        AgentPreset.workspace_id == AgentSession.workspace_id,
                        AgentPreset.deleted_at.is_(None),
                    ),
                )
                .where(
                    AgentSession.workspace_id == self.workspace_id,
                    AgentSession.id.in_(session_ids),
                )
            )
            for session_id, name, preset_id in rows.tuples():
                titles[session_id] = name
                presets[session_id] = preset_id
        filled: list[TriggerRunRead] = []
        for item in items:
            if item.session_id is not None:
                update = {
                    "target_name": titles.get(item.session_id),
                    "agent_preset_id": presets.get(item.session_id),
                }
            elif item.workflow_id is not None:
                update = {"target_name": titles.get(uuid.UUID(str(item.workflow_id)))}
            else:
                update = {}
            filled.append(item.model_copy(update=update))
        return filled

    async def list_stopped_events(
        self, trigger_id: uuid.UUID, params: CursorPaginationParams
    ) -> TriggerStoppedEventsPage:
        """Events this trigger matched that stopped retrying, newest first.

        Only ``object_storage`` triggers queue events; other kinds have none.
        A row is listed when it came through the trigger's source and its key
        starts with the trigger's prefix, as the dispatcher matches. An event
        is one row for every trigger on its source, so its error can name
        another trigger's targets in this workspace. The event body is never returned, and the error is redacted.

        Raises:
            ScopeDeniedError: Without the trigger's read scopes, or without
                both ``workflow:read`` and ``agent:read``.
            TriggerError: ``not_found`` if the workspace has no such trigger.
            PaginationError: If the cursor is not one this list returned.
        """
        # An error can describe a failed workflow or agent start, and a row
        # doesn't say which, so it needs both run read scopes.
        check_scopes(self.role, "workflow:read", "agent:read")
        trigger = await TriggersService(self.session, self.role).get_trigger(trigger_id)
        config = trigger.config
        if trigger.kind != TriggerKind.OBJECT_STORAGE or not isinstance(
            config, ObjectStorageTriggerConfigRead
        ):
            return TriggerStoppedEventsPage(items=[])
        filters = [
            EventOutbox.workspace_id == self.workspace_id,
            EventOutbox.source_id == config.source_id,
            EventOutbox.kind == TriggerKind.OBJECT_STORAGE.value,
            EventOutbox.next_attempt_at.is_(None),
            func.starts_with(EventOutbox.payload["key"].astext, config.key_prefix),
        ]
        # Paged on (received, occurrence key): the row ID comes from one
        # sequence across every workspace, so it is never shown or used.
        if params.cursor is not None:
            received, occurrence_key = _decode_stopped_cursor(params.cursor, trigger.id)
            filters.append(
                tuple_(EventOutbox.created_at, EventOutbox.occurrence_key)
                < tuple_(literal(received), literal(occurrence_key))
            )
        rows = (
            (
                await self.session.execute(
                    select(
                        EventOutbox.occurrence_key,
                        EventOutbox.attempts,
                        EventOutbox.last_error,
                        EventOutbox.created_at,
                        # Read only to blank its values out of the error.
                        EventOutbox.payload,
                    )
                    .where(and_(*filters))
                    .order_by(
                        EventOutbox.created_at.desc(), EventOutbox.occurrence_key.desc()
                    )
                    .limit(params.limit + 1)
                )
            )
            .tuples()
            .all()
        )
        has_more = len(rows) > params.limit
        rows = rows[: params.limit]
        events = [
            TriggerStoppedEventRead(
                attempts=attempts,
                error=_stopped_error(last_error, payload),
                created_at=created_at,
            )
            for _, attempts, last_error, created_at, payload in rows
        ]
        next_cursor = (
            _encode_stopped_cursor(trigger.id, rows[-1][3], rows[-1][0])
            if has_more
            else None
        )
        return TriggerStoppedEventsPage(
            items=events,
            next_cursor=next_cursor,
            has_more=has_more,
            # Forward only: no previous cursor is issued.
            has_previous=False,
        )

    async def run_origin(
        self,
        *,
        wf_exec_id: WorkflowExecutionID | None = None,
        session_id: uuid.UUID | None = None,
    ) -> TriggerRunOrigin | None:
        """The trigger that started a workflow run or an agent session, if any.

        A workflow run's child runs answer with their root's trigger; any
        other workflow type (an agent turn) answers null. A run a schedule
        started answers with the schedule's trigger while the schedule
        exists, for a caller with ``schedule:read``. A session answers with
        the trigger of any of its turns.

        Raises:
            ScopeDeniedError: Without ``workflow:read`` (a run) or
                ``agent:read`` (a session), or the trigger's read scopes.
            ValueError: Unless exactly one of the two is given.
        """
        if (wf_exec_id is None) == (session_id is None):
            raise ValueError("Give exactly one of wf_exec_id and session_id")
        check_scopes(
            self.role, "workflow:read" if wf_exec_id is not None else "agent:read"
        )
        raw: str | None = None
        if wf_exec_id is not None:
            executions = await WorkflowExecutionsService.connect(
                role=self.role, session=self.session
            )
            execution = await executions.get_execution(wf_exec_id)
            # Workflow runs only: an agent turn's ID would otherwise answer
            # for a session without agent:read.
            if execution is not None and execution.workflow_type == DSL_WORKFLOW_TYPE:
                attrs = execution.typed_search_attributes
                raw = attrs.get(TemporalSearchAttr.TRIGGER_ID.key)
                # Without schedule:read the schedule's trigger can't be
                # named, so the run answers as it did before schedules were
                # read: no trigger.
                if (
                    raw is None
                    and has_scope(self.role.scopes or frozenset(), "schedule:read")
                    and (scheduled_by := attrs.get(TEMPORAL_SCHEDULED_BY_ID))
                ):
                    trigger_of_schedule = await self._schedule_trigger_id(scheduled_by)
                    raw = str(trigger_of_schedule) if trigger_of_schedule else None
        elif session_id is not None:
            query = " AND ".join(
                (
                    build_query(
                        workspace_id=self.workspace_id,
                        correlation_id=build_agent_session_correlation_id(session_id),
                        # Agent turns only, as the run branch takes workflow
                        # runs only.
                        workflow_types=agent_turn_workflow_types(),
                    ),
                    f"{TemporalSearchAttr.TRIGGER_ID.value} IS NOT NULL",
                )
            )
            client = await get_temporal_client()
            try:
                async for execution in client.list_workflows(query, limit=1):
                    raw = execution.typed_search_attributes.get(
                        TemporalSearchAttr.TRIGGER_ID.key
                    )
            except RPCError as e:
                # Not registered yet: no session carries it.
                if e.status != RPCStatusCode.INVALID_ARGUMENT:
                    raise
        if raw is None:
            return None
        try:
            trigger_id = uuid.UUID(raw)
        except ValueError:
            return None
        try:
            trigger = await TriggersService(self.session, self.role).get_trigger(
                trigger_id
            )
        except TriggerError as e:
            if e.code != TriggerErrorCode.NOT_FOUND:
                raise
            return TriggerRunOrigin(trigger_id=trigger_id)
        return TriggerRunOrigin(
            trigger_id=trigger_id, name=trigger.name, kind=trigger.kind
        )

    async def _schedule_trigger_id(self, temporal_schedule_id: str) -> uuid.UUID | None:
        """The trigger of the schedule a ``TemporalScheduledById`` value names.

        Only this workspace's schedules answer, so a run naming another
        workspace's schedule never resolves.
        """
        if not temporal_schedule_id.startswith(LEGACY_SCHEDULE_ID_PREFIX):
            return None
        try:
            schedule_id = ScheduleUUID.new(temporal_schedule_id)
        except ValueError:
            return None
        return await self.session.scalar(
            select(Schedule.trigger_id).where(
                Schedule.workspace_id == self.workspace_id,
                Schedule.id == schedule_id,
            )
        )
