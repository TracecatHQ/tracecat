"""Test that updating a schedule from cron to interval properly clears cron expressions."""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import temporalio.client

from tracecat.identifiers import ScheduleUUID
from tracecat.workflow.schedules import bridge
from tracecat.workflow.schedules.schemas import ScheduleUpdate


@pytest.mark.anyio
async def test_update_schedule_from_cron_to_interval_clears_cron():
    """Test that updating a schedule from cron to interval clears existing cron expressions."""

    schedule_id = ScheduleUUID.new_uuid4()

    # Mock the Temporal handle
    mock_handle = MagicMock()
    mock_handle.update = AsyncMock()

    # Track what update function is called with
    captured_update = None

    async def capture_update(update_func):
        nonlocal captured_update
        # Create a mock input that simulates an existing schedule with cron
        mock_input = MagicMock()
        mock_schedule = MagicMock()
        mock_spec = MagicMock()

        # Simulate existing cron expression
        mock_spec.cron_expressions = ["0 9 * * *"]  # Daily at 9 AM
        mock_spec.intervals = []

        mock_schedule.spec = mock_spec
        mock_schedule.action = MagicMock(
            spec=temporalio.client.ScheduleActionStartWorkflow
        )
        mock_schedule.action.args = [MagicMock()]
        mock_schedule.action.args[0].dsl = MagicMock()
        mock_schedule.action.typed_search_attributes = {}
        mock_schedule.state = MagicMock()

        mock_input.description.schedule = mock_schedule

        # Call the update function to see what it does
        result = await update_func(mock_input)
        captured_update = result.schedule
        return result

    mock_handle.update = capture_update

    with patch(
        "tracecat.workflow.schedules.bridge._get_handle", return_value=mock_handle
    ):
        # Update with interval only (no cron field)
        update_params = ScheduleUpdate(
            every=timedelta(hours=1)  # Switch to hourly interval
        )

        await bridge.update_schedule(schedule_id, update_params)

        # Verify the update
        assert captured_update is not None

        # Check that cron expressions should be cleared (THIS WILL FAIL WITH CURRENT CODE)
        # The bug is that cron_expressions are not cleared when updating to interval
        assert captured_update.spec.cron_expressions == [], (
            f"Cron expressions should be cleared when switching to interval, "
            f"but got: {captured_update.spec.cron_expressions}"
        )

        # Check that interval is set correctly
        assert len(captured_update.spec.intervals) == 1
        assert captured_update.spec.intervals[0].every == timedelta(hours=1)


@pytest.mark.anyio
async def test_update_schedule_with_explicit_cron_clears_interval():
    """Test that updating a schedule with explicit cron clears intervals."""

    schedule_id = ScheduleUUID.new_uuid4()

    # Mock the Temporal handle
    mock_handle = MagicMock()
    mock_handle.update = AsyncMock()

    # Track what update function is called with
    captured_update = None

    async def capture_update(update_func):
        nonlocal captured_update
        # Create a mock input that simulates an existing schedule with interval
        mock_input = MagicMock()
        mock_schedule = MagicMock()
        mock_spec = MagicMock()

        # Simulate existing interval
        mock_spec.cron_expressions = []
        mock_interval = MagicMock()
        mock_interval.every = timedelta(hours=1)
        mock_interval.offset = None
        mock_spec.intervals = [mock_interval]

        mock_schedule.spec = mock_spec
        mock_schedule.action = MagicMock(
            spec=temporalio.client.ScheduleActionStartWorkflow
        )
        mock_schedule.action.args = [MagicMock()]
        mock_schedule.action.args[0].dsl = MagicMock()
        mock_schedule.action.typed_search_attributes = {}
        mock_schedule.state = MagicMock()

        mock_input.description.schedule = mock_schedule

        # Call the update function to see what it does
        result = await update_func(mock_input)
        captured_update = result.schedule
        return result

    mock_handle.update = capture_update

    with patch(
        "tracecat.workflow.schedules.bridge._get_handle", return_value=mock_handle
    ):
        # Update with cron expression
        update_params = ScheduleUpdate(
            cron="0 9 * * *"  # Daily at 9 AM
        )

        await bridge.update_schedule(schedule_id, update_params)

        # Verify the update
        assert captured_update is not None

        # Check that interval is cleared when cron is set
        assert captured_update.spec.intervals == []

        # Check that cron is set correctly
        assert captured_update.spec.cron_expressions == ["0 9 * * *"]


@pytest.mark.anyio
async def test_update_schedule_preserves_existing_when_neither_provided():
    """Test that not providing cron or interval preserves existing schedule type."""

    schedule_id = ScheduleUUID.new_uuid4()

    # Mock the Temporal handle
    mock_handle = MagicMock()
    mock_handle.update = AsyncMock()

    # Track what update function is called with
    captured_update = None

    async def capture_update(update_func):
        nonlocal captured_update
        # Create a mock input that simulates an existing schedule with cron
        mock_input = MagicMock()
        mock_schedule = MagicMock()
        mock_spec = MagicMock()

        # Simulate existing cron expression
        mock_spec.cron_expressions = ["0 9 * * *"]
        mock_spec.intervals = []

        mock_schedule.spec = mock_spec
        mock_schedule.action = MagicMock(
            spec=temporalio.client.ScheduleActionStartWorkflow
        )
        mock_schedule.action.args = [MagicMock()]
        mock_schedule.action.args[0].dsl = MagicMock()
        mock_schedule.action.typed_search_attributes = {}
        mock_schedule.state = MagicMock()

        mock_input.description.schedule = mock_schedule

        # Call the update function to see what it does
        result = await update_func(mock_input)
        captured_update = result.schedule
        return result

    mock_handle.update = capture_update

    with patch(
        "tracecat.workflow.schedules.bridge._get_handle", return_value=mock_handle
    ):
        # Update only status, not schedule spec
        update_params = ScheduleUpdate(status="offline")

        await bridge.update_schedule(schedule_id, update_params)

        # Verify the update
        assert captured_update is not None

        # Check that cron expressions are preserved when neither cron nor interval is provided
        assert captured_update.spec.cron_expressions == ["0 9 * * *"]
        assert captured_update.spec.intervals == []

        # Check that status was updated
        assert captured_update.state.paused is True
