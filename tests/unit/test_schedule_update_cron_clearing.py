"""Test that updating a schedule from cron to interval properly clears cron expressions."""

from datetime import timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import temporalio.client

from tracecat.identifiers import ScheduleUUID
from tracecat.workflow.schedules import bridge
from tracecat.workflow.schedules.schemas import ScheduleUpdate


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("existing_cron", "existing_every", "update", "expected_cron", "expected_every"),
    [
        pytest.param(
            ["0 9 * * *"],
            [],
            ScheduleUpdate(every=timedelta(hours=1)),
            [],
            [timedelta(hours=1)],
            id="cron_to_interval_clears_cron",
        ),
        pytest.param(
            [],
            [timedelta(hours=1)],
            ScheduleUpdate(cron="0 9 * * *"),
            ["0 9 * * *"],
            [],
            id="explicit_cron_clears_interval",
        ),
        pytest.param(
            ["0 9 * * *"],
            [],
            ScheduleUpdate(status="offline"),
            ["0 9 * * *"],
            [],
            id="neither_provided_preserves_existing",
        ),
    ],
)
async def test_update_schedule_spec(
    existing_cron: list[str],
    existing_every: list[timedelta],
    update: ScheduleUpdate,
    expected_cron: list[str],
    expected_every: list[timedelta],
):
    """Switching between cron and interval clears the other; updates that set
    neither preserve the existing schedule type."""
    captured_update: Any = None

    async def capture_update(update_func):
        nonlocal captured_update
        mock_input = MagicMock()
        mock_schedule = MagicMock()
        mock_spec = MagicMock()
        mock_spec.cron_expressions = existing_cron
        mock_spec.intervals = [
            MagicMock(every=every, offset=None) for every in existing_every
        ]

        mock_schedule.spec = mock_spec
        mock_schedule.action = MagicMock(
            spec=temporalio.client.ScheduleActionStartWorkflow
        )
        mock_schedule.action.args = [MagicMock()]
        mock_schedule.action.args[0].dsl = MagicMock()
        mock_schedule.action.typed_search_attributes = {}
        mock_schedule.state = MagicMock()

        mock_input.description.schedule = mock_schedule

        result = await update_func(mock_input)
        captured_update = result.schedule
        return result

    mock_handle = MagicMock()
    mock_handle.update = capture_update

    with patch(
        "tracecat.workflow.schedules.bridge._get_handle", return_value=mock_handle
    ):
        await bridge.update_schedule(ScheduleUUID.new_uuid4(), update)

    assert captured_update is not None
    assert captured_update.spec.cron_expressions == expected_cron
    assert [i.every for i in captured_update.spec.intervals] == expected_every
    if update.status == "offline":
        assert captured_update.state.paused is True
