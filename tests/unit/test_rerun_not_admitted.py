"""Rerun source and history views after an executor did not admit an attempt."""

from __future__ import annotations

import datetime
import uuid
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from temporalio.api.enums.v1 import EventType
from temporalio.api.history.v1 import HistoryEvent

from tracecat.admission.types import ConcurrencyResource, NotAdmitted
from tracecat.auth.types import Role
from tracecat.dsl._converter import get_data_converter
from tracecat.dsl.common import create_default_execution_context
from tracecat.dsl.schemas import (
    ROOT_STREAM,
    ActionStatement,
    RunActionInput,
    RunContext,
)
from tracecat.identifiers.workflow import WorkflowUUID, generate_exec_id
from tracecat.registry.lock.types import RegistryLock
from tracecat.storage.object import InlineObject
from tracecat.workflow.executions.enums import (
    WorkflowEventType,
    WorkflowExecutionEventStatus,
)
from tracecat.workflow.executions.schemas import (
    EventGroup,
    WorkflowExecutionEventCompact,
)
from tracecat.workflow.executions.service import WorkflowExecutionsService

_CONVERTER = get_data_converter(compression_enabled=False)


def _scheduled(event_id: int) -> HistoryEvent:
    event = HistoryEvent(
        event_id=event_id, event_type=EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
    )
    attrs = event.activity_task_scheduled_event_attributes
    attrs.activity_type.name = "execute_action_activity"
    attrs.activity_id = str(event_id)
    return event


def _completed(event_id: int, scheduled_id: int, result: Any) -> HistoryEvent:
    event = HistoryEvent(
        event_id=event_id, event_type=EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED
    )
    attrs = event.activity_task_completed_event_attributes
    attrs.scheduled_event_id = scheduled_id
    attrs.result.payloads.extend(_CONVERTER.payload_converter.to_payloads([result]))
    return event


def _source(event: HistoryEvent) -> WorkflowExecutionEventCompact:
    return WorkflowExecutionEventCompact(
        source_event_id=event.event_id,
        schedule_time=datetime.datetime.now(datetime.UTC),
        curr_event_type=WorkflowEventType.ACTIVITY_TASK_SCHEDULED,
        status=WorkflowExecutionEventStatus.SCHEDULED,
        action_name="core.transform.reshape",
        action_ref="poll",
        action_input={"value": 1},
        stream_id=ROOT_STREAM,
    )


async def _rerun_source(not_admitted_attempt: int) -> Any:
    """A retry_until action ran once, then its next execution wasn't admitted."""
    ran = _scheduled(1)
    bounced = _scheduled(3)
    history = [
        ran,
        _completed(2, 1, InlineObject(data={"status": "loading"})),
        bounced,
        _completed(
            4,
            3,
            NotAdmitted(
                resource=ConcurrencyResource.ACTIONS,
                in_use=1,
                limit=1,
                attempt=not_admitted_attempt,
            ),
        ),
    ]

    async def fetch_history_events(**_: Any) -> AsyncIterator[HistoryEvent]:
        for event in history:
            yield event

    handle = MagicMock()
    handle.fetch_history_events = fetch_history_events
    role = Role(
        type="service",
        service_id="tracecat-runner",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )
    service = WorkflowExecutionsService(MagicMock(), role=role)
    exec_id = generate_exec_id(WorkflowUUID.new_uuid4())
    with (
        patch.object(service, "require_execution", new=AsyncMock()),
        patch.object(service, "handle", return_value=handle),
        patch.object(
            WorkflowExecutionEventCompact,
            "from_source_event",
            new=AsyncMock(side_effect=[_source(ran), _source(bounced)]),
        ),
    ):
        return await service.get_rerun_source(exec_id)


@pytest.mark.anyio
async def test_first_attempt_not_admitted_keeps_the_earlier_execution() -> None:
    """Nothing ran in the bounced activity: the earlier execution is reusable."""
    source = await _rerun_source(not_admitted_attempt=1)
    assert source.actions["poll"].fixed is not None
    assert source.actions["poll"].fixed.source_event_id == 1


@pytest.mark.anyio
async def test_later_attempt_not_admitted_offers_no_stale_result() -> None:
    """Attempt 1 of the bounced activity ran and failed: nothing is reusable."""
    source = await _rerun_source(not_admitted_attempt=2)
    assert source.actions["poll"].fixed is None


def _not_admitted_history(attempt: int) -> list[HistoryEvent]:
    """One executor activity, completed with a not-admitted answer on ``attempt``."""
    return [
        _scheduled(1),
        _completed(
            2,
            1,
            NotAdmitted(
                resource=ConcurrencyResource.ACTIONS,
                in_use=2,
                limit=2,
                attempt=attempt,
            ),
        ),
    ]


def _run_action_input() -> RunActionInput:
    wf_id = WorkflowUUID.new_uuid4()
    return RunActionInput(
        task=ActionStatement(
            ref="poll", action="core.transform.reshape", args={"value": 1}
        ),
        exec_context=create_default_execution_context(),
        run_context=RunContext(
            wf_id=wf_id,
            wf_exec_id=f"{wf_id.short()}/exec_test",
            wf_run_id=uuid.uuid4(),
            environment="test",
            logical_time=datetime.datetime.now(datetime.UTC),
        ),
        registry_lock=RegistryLock(
            origins={"tracecat_registry": "test-version"},
            actions={"core.transform.reshape": "tracecat_registry"},
        ),
    )


def _service() -> WorkflowExecutionsService:
    role = Role(
        type="service",
        service_id="tracecat-runner",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
    )
    return WorkflowExecutionsService(MagicMock(), role=role)


async def _compact(attempt: int) -> list[WorkflowExecutionEventCompact]:
    history = _not_admitted_history(attempt)

    async def fetch_history_events(**_: Any) -> AsyncIterator[HistoryEvent]:
        for event in history:
            yield event

    handle = MagicMock()
    handle.fetch_history_events = fetch_history_events
    handle.describe = AsyncMock(
        return_value=MagicMock(raw_description=MagicMock(pending_activities=[]))
    )
    service = _service()
    with (
        patch.object(service, "require_execution", new=AsyncMock()),
        patch.object(service, "handle", return_value=handle),
        patch.object(
            WorkflowExecutionEventCompact,
            "from_source_event",
            new=AsyncMock(return_value=_source(history[0])),
        ),
    ):
        return await service.list_workflow_execution_events_compact(
            generate_exec_id(WorkflowUUID.new_uuid4())
        )


async def _detailed(attempt: int) -> list[Any]:
    history = MagicMock(events=_not_admitted_history(attempt))
    handle = MagicMock()
    handle.fetch_history = AsyncMock(return_value=history)
    group = EventGroup.model_construct(
        event_id=1,
        udf_namespace="core.transform",
        udf_name="reshape",
        udf_key="core.transform.reshape",
        action_ref="poll",
        action_input=_run_action_input(),
    )
    service = _service()
    with (
        patch.object(service, "require_execution", new=AsyncMock()),
        patch.object(service, "handle", return_value=handle),
        patch.object(
            EventGroup, "from_scheduled_activity", new=AsyncMock(return_value=group)
        ),
    ):
        return await service.list_workflow_execution_events(
            generate_exec_id(WorkflowUUID.new_uuid4())
        )


@pytest.mark.anyio
async def test_history_hides_an_activity_that_ran_nothing() -> None:
    assert [e for e in await _compact(1) if e.action_ref == "poll"] == []
    assert await _detailed(1) == []


@pytest.mark.anyio
async def test_history_shows_a_later_attempt_not_admitted_as_failed() -> None:
    """Attempt 1 ran and failed, attempt 2 was not admitted: never hide it."""
    (row,) = [e for e in await _compact(2) if e.action_ref == "poll"]
    assert row.status is WorkflowExecutionEventStatus.FAILED
    assert row.action_error is not None
    assert "Attempt 2 was not admitted" in row.action_error.message
    assert row.action_result is None

    events = await _detailed(2)
    assert [e.event_type for e in events] == [
        WorkflowEventType.ACTIVITY_TASK_SCHEDULED,
        WorkflowEventType.ACTIVITY_TASK_COMPLETED,
    ]
