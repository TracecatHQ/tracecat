"""Schedule overlap policy follows TRACECAT__SCHEDULE_OVERLAP_POLICY."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import temporalio.client

from tracecat import config
from tracecat.auth.types import Role
from tracecat.identifiers import ScheduleUUID
from tracecat.identifiers.workflow import WorkflowUUID, WorkspaceUUID
from tracecat.workflow.schedules import bridge
from tracecat.workflow.schedules.schemas import ScheduleUpdate

SKIP = temporalio.client.ScheduleOverlapPolicy.SKIP
ALLOW_ALL = temporalio.client.ScheduleOverlapPolicy.ALLOW_ALL


def test_overlap_policy_defaults_to_allow_all() -> None:
    assert config.TRACECAT__SCHEDULE_OVERLAP_POLICY == "ALLOW_ALL"
    assert bridge._overlap_policy() is ALLOW_ALL


def test_overlap_policy_follows_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "TRACECAT__SCHEDULE_OVERLAP_POLICY", "SKIP")
    assert bridge._overlap_policy() is SKIP


@pytest.mark.anyio
async def test_create_schedule_uses_configured_overlap_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__SCHEDULE_OVERLAP_POLICY", "SKIP")
    captured: dict[str, temporalio.client.Schedule] = {}

    class _FakeTemporalClient:
        async def create_schedule(
            self,
            id: str,
            schedule: temporalio.client.Schedule,
        ) -> Any:
            captured["schedule"] = schedule
            return SimpleNamespace(id=id)

    async def _get_temporal_client() -> _FakeTemporalClient:
        return _FakeTemporalClient()

    monkeypatch.setattr(bridge, "get_temporal_client", _get_temporal_client)

    await bridge.create_schedule(
        workflow_id=WorkflowUUID.new_uuid4(),
        schedule_id=ScheduleUUID.new_uuid4(),
        role=Role(
            type="service",
            service_id="tracecat-service",
            workspace_id=WorkspaceUUID.new_uuid4(),
        ),
        every=timedelta(seconds=47),
    )

    assert captured["schedule"].policy.overlap == SKIP


async def _update_existing_schedule(
    params: ScheduleUpdate,
    policy: temporalio.client.SchedulePolicy,
) -> temporalio.client.Schedule:
    """Run bridge.update_schedule against a schedule that already holds `policy`."""
    captured: dict[str, temporalio.client.Schedule] = {}

    async def capture_update(update_func: Any) -> Any:
        mock_schedule = MagicMock()
        mock_schedule.spec = MagicMock()
        mock_schedule.spec.cron_expressions = []
        mock_schedule.spec.intervals = []
        mock_schedule.action = MagicMock(
            spec=temporalio.client.ScheduleActionStartWorkflow
        )
        mock_schedule.action.args = [MagicMock()]
        mock_schedule.action.typed_search_attributes = {}
        mock_schedule.state = MagicMock()
        mock_schedule.policy = policy

        mock_input = MagicMock()
        mock_input.description.schedule = mock_schedule

        result = await update_func(mock_input)
        captured["schedule"] = result.schedule
        return result

    mock_handle = MagicMock()
    mock_handle.update = capture_update

    with patch(
        "tracecat.workflow.schedules.bridge._get_handle", return_value=mock_handle
    ):
        await bridge.update_schedule(ScheduleUUID.new_uuid4(), params)

    return captured["schedule"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "params",
    [
        ScheduleUpdate(status="online"),
        ScheduleUpdate(status="offline"),
        ScheduleUpdate(every=timedelta(minutes=5)),
    ],
    ids=["go-online", "go-offline", "change-interval"],
)
async def test_update_schedule_reapplies_configured_policy_to_an_old_schedule(
    params: ScheduleUpdate,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__SCHEDULE_OVERLAP_POLICY", "SKIP")
    old_policy = temporalio.client.SchedulePolicy(
        overlap=ALLOW_ALL,
        catchup_window=timedelta(days=1),
        pause_on_failure=True,
    )

    schedule = await _update_existing_schedule(params, old_policy)

    assert schedule.policy.overlap == SKIP
    # only the overlap policy is replaced
    assert schedule.policy.catchup_window == timedelta(days=1)
    assert schedule.policy.pause_on_failure is True
