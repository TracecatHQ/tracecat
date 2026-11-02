"""HTTP-level tests for schedule API endpoints."""

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from tracecat.auth.types import Role


@pytest.mark.anyio
async def test_malformed_schedule_id_is_validation_error(
    client: TestClient, test_admin_role: Role
) -> None:
    response = client.get(
        "/schedules/not-a-schedule-id",
        params={"workspace_id": str(test_admin_role.workspace_id)},
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert response.json()["detail"][0]["loc"] == ["path", "schedule_id"]
