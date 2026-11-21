"""Rerun source after an executor did not admit an attempt."""

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
from tracecat.dsl.schemas import ROOT_STREAM
from tracecat.identifiers.workflow import WorkflowUUID, generate_exec_id
from tracecat.storage.object import InlineObject
from tracecat.workflow.executions.enums import (
    WorkflowEventType,
    WorkflowExecutionEventStatus,
)
from tracecat.workflow.executions.schemas import WorkflowExecutionEventCompact
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
