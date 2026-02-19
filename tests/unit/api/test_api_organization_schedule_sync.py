"""HTTP-level tests for organization schedule sync endpoints."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from tracecat.auth.types import Role
from tracecat.identifiers import ScheduleUUID, WorkflowID
from tracecat.organization import router as organization_router
from tracecat.organization.schemas import (
    OrgScheduleRecreateAction,
    OrgScheduleRecreateResponse,
    OrgScheduleRecreateResult,
    OrgScheduleTemporalItem,
    OrgScheduleTemporalStatus,
    OrgScheduleTemporalSummary,
    OrgScheduleTemporalSyncRead,
)


@pytest.mark.anyio
async def test_get_temporal_schedule_sync_status_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    schedule_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    response_model = OrgScheduleTemporalSyncRead(
        summary=OrgScheduleTemporalSummary(
            total_schedules=1,
            present_count=1,
            missing_count=0,
        ),
        items=[
            OrgScheduleTemporalItem(
                schedule_id=ScheduleUUID.new(schedule_id),
                workspace_id=workspace_id,
                workspace_name="Default workspace",
                workflow_id=WorkflowID.new(workflow_id),
                workflow_title="Daily workflow",
                db_status="online",
                temporal_status=OrgScheduleTemporalStatus.PRESENT,
                last_checked_at=datetime.now(UTC),
                error=None,
            )
        ],
    )

    with patch.object(
        organization_router, "OrganizationScheduleSyncService"
    ) as mock_service_cls:
        mock_service = AsyncMock()
        mock_service.get_temporal_sync_status.return_value = response_model
        mock_service_cls.return_value = mock_service

        response = client.get("/organization/schedules/temporal-sync")

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()
    assert payload["summary"]["total_schedules"] == 1
    assert payload["summary"]["present_count"] == 1
    assert payload["summary"]["missing_count"] == 0
    assert payload["items"][0]["schedule_id"] == str(schedule_id)
    assert payload["items"][0]["workspace_id"] == str(workspace_id)
    assert payload["items"][0]["workflow_id"] == str(workflow_id)
    assert payload["items"][0]["temporal_status"] == "present"


@pytest.mark.anyio
async def test_recreate_missing_temporal_schedules_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    schedule_id = uuid.uuid4()
    response_model = OrgScheduleRecreateResponse(
        processed_count=1,
        created_count=1,
        already_present_count=0,
        failed_count=0,
        results=[
            OrgScheduleRecreateResult(
                schedule_id=ScheduleUUID.new(schedule_id),
                action=OrgScheduleRecreateAction.CREATED,
            )
        ],
    )

    with patch.object(
        organization_router, "OrganizationScheduleSyncService"
    ) as mock_service_cls:
        mock_service = AsyncMock()
        mock_service.recreate_missing_temporal_schedules.return_value = response_model
        mock_service_cls.return_value = mock_service

        response = client.post(
            "/organization/schedules/temporal-sync/recreate-missing",
            json={"schedule_ids": [str(schedule_id)]},
        )

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()
    assert payload["processed_count"] == 1
    assert payload["created_count"] == 1
    assert payload["already_present_count"] == 0
    assert payload["failed_count"] == 0
    assert payload["results"][0]["schedule_id"] == str(schedule_id)
    assert payload["results"][0]["action"] == "created"
