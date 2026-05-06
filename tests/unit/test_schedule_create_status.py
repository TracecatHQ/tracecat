from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import Any, Literal

import pytest
import temporalio.client

from tracecat.auth.types import Role
from tracecat.identifiers import ScheduleUUID
from tracecat.identifiers.workflow import WorkflowUUID, WorkspaceUUID
from tracecat.workflow.schedules import bridge


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "expected_paused"),
    [
        ("online", False),
        ("offline", True),
    ],
)
async def test_create_schedule_sets_temporal_paused_state(
    monkeypatch: pytest.MonkeyPatch,
    status: Literal["online", "offline"],
    expected_paused: bool,
) -> None:
    captured: dict[str, temporalio.client.Schedule] = {}

    class _FakeTemporalClient:
        async def create_schedule(
            self,
            id: str,
            schedule: temporalio.client.Schedule,
        ) -> Any:
            _ = id
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
        every=timedelta(hours=1),
        status=status,
    )

    assert captured["schedule"].state.paused is expected_paused
