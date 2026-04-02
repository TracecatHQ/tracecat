"""Regression tests for schedule update search attributes."""

from __future__ import annotations

import uuid
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest
import temporalio.client
from temporalio.common import TypedSearchAttributes

from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.identifiers import ScheduleUUID
from tracecat.workflow.executions.enums import (
    ExecutionType,
    TemporalSearchAttr,
    TriggerType,
)
from tracecat.workflow.schedules import bridge
from tracecat.workflow.schedules.schemas import ScheduleUpdate


def _build_mock_schedule_input() -> MagicMock:
    mock_input = MagicMock()
    mock_schedule = MagicMock()
    mock_spec = MagicMock()

    mock_spec.cron_expressions = []
    mock_spec.intervals = []
    mock_schedule.spec = mock_spec
    mock_schedule.action = MagicMock(spec=temporalio.client.ScheduleActionStartWorkflow)
    mock_schedule.action.args = [MagicMock()]
    mock_schedule.action.args[0].dsl = MagicMock()
    mock_schedule.action.typed_search_attributes = None
    mock_schedule.state = MagicMock()
    mock_input.description.schedule = mock_schedule
    return mock_input


@pytest.mark.anyio
async def test_update_schedule_preserves_workspace_search_attrs_when_decode_fails():
    """Use the caller role when rebuilding search attributes after decode failure."""

    schedule_id = ScheduleUUID.new_uuid4()
    workspace_id = uuid.uuid4()
    role = Role(
        type="service",
        workspace_id=workspace_id,
        service_id="tracecat-service",
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-service"],
    )

    captured_update = None

    async def capture_update(update_func):
        nonlocal captured_update
        result = await update_func(_build_mock_schedule_input())
        captured_update = result.schedule
        return result

    mock_handle = MagicMock()
    mock_handle.update = capture_update

    with (
        patch(
            "tracecat.workflow.schedules.bridge._get_handle", return_value=mock_handle
        ),
        patch(
            "tracecat.workflow.executions.common.extract_first",
            side_effect=RuntimeError("cannot decode existing schedule args"),
        ),
    ):
        await bridge.update_schedule(
            schedule_id, ScheduleUpdate(status="offline"), role=role
        )

    assert captured_update is not None
    search_attrs = captured_update.action.typed_search_attributes
    assert isinstance(search_attrs, TypedSearchAttributes)

    pairs = cast(list[Any], search_attrs.search_attributes)
    values = {pair.key.name: pair.value for pair in pairs}
    assert values[TemporalSearchAttr.TRIGGER_TYPE.value] == TriggerType.SCHEDULED.value
    assert (
        values[TemporalSearchAttr.EXECUTION_TYPE.value] == ExecutionType.PUBLISHED.value
    )
    assert values[TemporalSearchAttr.WORKSPACE_ID.value] == str(workspace_id)
    assert captured_update.state.paused is True
