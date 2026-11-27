"""API models for a trigger's recent runs, its stopped events and a run's trigger."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from tracecat.core.schemas import Schema
from tracecat.identifiers.workflow import AnyWorkflowID
from tracecat.pagination import CursorPaginatedResponse
from tracecat.triggers.enums import TriggerKind, TriggerTargetType
from tracecat.workflow.executions.enums import WorkflowExecutionStatusLiteral


class TriggerRunRead(Schema):
    """One run a trigger started: a workflow run or an agent session's turn."""

    target_type: TriggerTargetType
    target_name: str | None = Field(
        default=None,
        description="The workflow's title (null if it was deleted), or for an"
        " agent run the agent preset's name, falling back to the session's title"
        " if the preset was deleted (null if the session was too)",
    )
    wf_exec_id: str = Field(description="The run's Temporal workflow ID")
    workflow_id: AnyWorkflowID | None = Field(
        default=None, description="The workflow, for a workflow run"
    )
    session_id: uuid.UUID | None = Field(
        default=None, description="The agent session, for an agent run"
    )
    agent_preset_id: uuid.UUID | None = Field(
        default=None, description="The session's agent preset, for an agent run"
    )
    status: WorkflowExecutionStatusLiteral
    start_time: datetime
    close_time: datetime | None = Field(default=None)


class TriggerRunsPage(CursorPaginatedResponse[TriggerRunRead]):
    """A page of a trigger's runs, open runs first, then by close time."""


class TriggerStoppedEventRead(Schema):
    """An event that stopped retrying after the attempt cap. The event itself
    is not returned."""

    attempts: int
    error: str | None = Field(
        default=None, description="The last attempt's error, credentials redacted"
    )
    created_at: datetime = Field(description="When the event was received")


class TriggerStoppedEventsPage(CursorPaginatedResponse[TriggerStoppedEventRead]):
    """A page of a trigger's stopped events, newest first."""


class TriggerRunOrigin(Schema):
    """The trigger that started a run or an agent session."""

    trigger_id: uuid.UUID
    name: str | None = Field(
        default=None, description="Null if the trigger was deleted since"
    )
    kind: TriggerKind | None = Field(default=None)
