"""HTTP-level tests for admin tiers API endpoints."""

import uuid

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from tracecat import config as tracecat_config
from tracecat.auth.types import Role


@pytest.mark.anyio
async def test_create_tier_blocked_without_multi_tenant(
    client: TestClient, test_admin_role: Role, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tracecat_config, "TRACECAT__EE_MULTI_TENANT", False)

    response = client.post(
        "/admin/tiers",
        json={"display_name": "Blocked Tier"},
    )

    assert response.status_code == status.HTTP_402_PAYMENT_REQUIRED


@pytest.mark.anyio
async def test_update_org_tier_blocked_without_multi_tenant(
    client: TestClient, test_admin_role: Role, monkeypatch: pytest.MonkeyPatch
) -> None:
    org_id = uuid.uuid4()
    monkeypatch.setattr(tracecat_config, "TRACECAT__EE_MULTI_TENANT", False)

    response = client.patch(
        f"/admin/tiers/organizations/{org_id}",
        json={},
    )

    assert response.status_code == status.HTTP_402_PAYMENT_REQUIRED
