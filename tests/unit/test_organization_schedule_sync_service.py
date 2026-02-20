"""Tests for organization schedule Temporal sync service."""

import uuid
from datetime import timedelta
from typing import Literal
from unittest.mock import AsyncMock, patch

import pytest

from tracecat.auth.types import Role
from tracecat.identifiers import ScheduleUUID, WorkflowID
from tracecat.organization.schedule_sync_service import (
    OrganizationScheduleRecord,
    OrganizationScheduleSyncService,
)
from tracecat.organization.schemas import OrgScheduleTemporalStatus


def _record(
    *,
    status: Literal["online", "offline"] = "online",
) -> OrganizationScheduleRecord:
    return OrganizationScheduleRecord(
        schedule_id=ScheduleUUID.new(uuid.uuid4()),
        workspace_id=uuid.uuid4(),
        workspace_name="Default workspace",
        workflow_id=WorkflowID.new(uuid.uuid4()),
        workflow_title="Test workflow",
        db_status=status,
        cron="0 * * * *",
        every=timedelta(hours=1),
        offset=None,
        start_at=None,
        end_at=None,
        timeout=60.0,
    )


@pytest.mark.anyio
async def test_recreate_missing_schedules_pauses_offline_schedule(
    test_admin_role: Role,
) -> None:
    service = OrganizationScheduleSyncService(AsyncMock(), role=test_admin_role)
    schedule = _record(status="offline")

    with (
        patch.object(
            service, "_list_schedule_records", AsyncMock(return_value=[schedule])
        ),
        patch(
            "tracecat.organization.schedule_sync_service.get_temporal_client",
            AsyncMock(return_value=AsyncMock()),
        ),
        patch.object(
            service,
            "_check_temporal_schedule",
            AsyncMock(return_value=(OrgScheduleTemporalStatus.MISSING, None)),
        ),
        patch(
            "tracecat.organization.schedule_sync_service.bridge.create_schedule",
            AsyncMock(return_value=AsyncMock(id="sch-test")),
        ) as mock_create,
    ):
        response = await service.recreate_missing_temporal_schedules()

    assert response.created_count == 1
    assert response.failed_count == 0
    assert mock_create.await_args is not None
    assert mock_create.await_args.kwargs["paused"] is True


@pytest.mark.anyio
async def test_recreate_missing_schedules_skips_existing_schedule(
    test_admin_role: Role,
) -> None:
    service = OrganizationScheduleSyncService(AsyncMock(), role=test_admin_role)
    schedule = _record(status="online")

    with (
        patch.object(
            service, "_list_schedule_records", AsyncMock(return_value=[schedule])
        ),
        patch(
            "tracecat.organization.schedule_sync_service.get_temporal_client",
            AsyncMock(return_value=AsyncMock()),
        ),
        patch.object(
            service,
            "_check_temporal_schedule",
            AsyncMock(return_value=(OrgScheduleTemporalStatus.PRESENT, None)),
        ),
        patch(
            "tracecat.organization.schedule_sync_service.bridge.create_schedule",
            AsyncMock(return_value=AsyncMock(id="sch-test")),
        ) as mock_create,
    ):
        response = await service.recreate_missing_temporal_schedules()

    assert response.created_count == 0
    assert response.already_present_count == 1
    mock_create.assert_not_called()
