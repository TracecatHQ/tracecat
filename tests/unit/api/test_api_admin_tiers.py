"""HTTP-level tests for admin tiers API endpoints."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from tracecat_ee.admin.tiers import router as tiers_router

from tracecat.auth.types import Role
from tracecat.tiers.schemas import OrganizationTierRead, TierRead


def _tier_read(tier_id: uuid.UUID | None = None) -> TierRead:
    now = datetime(2024, 1, 1, tzinfo=UTC)
    return TierRead(
        id=tier_id or uuid.uuid4(),
        display_name="Test Tier",
        max_concurrent_workflows=None,
        max_action_executions_per_workflow=None,
        max_concurrent_actions=None,
        api_rate_limit=None,
        api_burst_capacity=None,
        entitlements={},
        is_default=False,
        sort_order=0,
        is_active=True,
        created_at=now,
        updated_at=now,
    )


def _org_tier_read(org_id: uuid.UUID, tier: TierRead) -> OrganizationTierRead:
    now = datetime(2024, 1, 1, tzinfo=UTC)
    return OrganizationTierRead(
        id=uuid.uuid4(),
        organization_id=org_id,
        tier_id=tier.id,
        max_concurrent_workflows=None,
        max_action_executions_per_workflow=None,
        max_concurrent_actions=None,
        api_rate_limit=None,
        api_burst_capacity=None,
        entitlement_overrides=None,
        stripe_customer_id=None,
        stripe_subscription_id=None,
        expires_at=None,
        created_at=now,
        updated_at=now,
        tier=tier,
    )


@pytest.mark.anyio
async def test_list_org_tiers_success(
    client: TestClient, test_admin_role: Role
) -> None:
    org_id = uuid.uuid4()
    tier = _tier_read()
    org_tier = _org_tier_read(org_id, tier)

    with patch.object(tiers_router, "AdminTierService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.list_org_tiers.return_value = [org_tier]
        MockService.return_value = mock_svc

        response = client.get(
            "/admin/tiers/organizations",
            params=[("org_ids", str(org_id))],
        )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data[0]["organization_id"] == str(org_id)
    assert data[0]["tier"]["id"] == str(tier.id)


@pytest.mark.anyio
async def test_create_tier_blocked_without_multi_tenant(
    client: TestClient, test_admin_role: Role, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tiers_router.config, "TRACECAT__EE_MULTI_TENANT", False)

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
    monkeypatch.setattr(tiers_router.config, "TRACECAT__EE_MULTI_TENANT", False)

    response = client.patch(
        f"/admin/tiers/organizations/{org_id}",
        json={},
    )

    assert response.status_code == status.HTTP_402_PAYMENT_REQUIRED
