"""HTTP-level tests for admin organizations API endpoints."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from tracecat_ee.admin.organizations import router as organizations_router
from tracecat_ee.admin.organizations.schemas import OrgRead
from tracecat_ee.admin.organizations.types import TierChangeResult

from tracecat.auth.types import Role
from tracecat.ee.compute.schemas import Tier


def _org_read(org_id: uuid.UUID | None = None, tier: Tier = Tier.STARTER) -> OrgRead:
    now = datetime(2024, 1, 1, tzinfo=UTC)
    return OrgRead(
        id=org_id or uuid.uuid4(),
        name="Test Org",
        slug="test-org",
        is_active=True,
        tier=tier,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.anyio
async def test_list_organizations_success(
    client: TestClient, test_admin_role: Role
) -> None:
    org = _org_read()

    with patch.object(organizations_router, "AdminOrgService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.list_organizations.return_value = [org]
        MockService.return_value = mock_svc

        response = client.get("/admin/organizations")

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data[0]["id"] == str(org.id)
    assert data[0]["slug"] == org.slug


@pytest.mark.anyio
async def test_create_organization_success(
    client: TestClient, test_admin_role: Role
) -> None:
    org = _org_read()

    with patch.object(organizations_router, "AdminOrgService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.create_organization.return_value = org
        MockService.return_value = mock_svc

        response = client.post(
            "/admin/organizations",
            json={"name": "Test Org", "slug": "test-org"},
        )

    assert response.status_code == status.HTTP_201_CREATED
    assert response.json()["slug"] == org.slug


@pytest.mark.anyio
async def test_get_organization_not_found(
    client: TestClient, test_admin_role: Role
) -> None:
    org_id = uuid.uuid4()

    with patch.object(organizations_router, "AdminOrgService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.get_organization.side_effect = ValueError("not found")
        MockService.return_value = mock_svc

        response = client.get(f"/admin/organizations/{org_id}")

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_update_organization_conflict(
    client: TestClient, test_admin_role: Role
) -> None:
    org_id = uuid.uuid4()

    with patch.object(organizations_router, "AdminOrgService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.update_organization.side_effect = ValueError("slug already exists")
        MockService.return_value = mock_svc

        response = client.patch(
            f"/admin/organizations/{org_id}",
            json={"slug": "test-org"},
        )

    assert response.status_code == status.HTTP_409_CONFLICT


@pytest.mark.anyio
async def test_delete_organization_success(
    client: TestClient, test_admin_role: Role
) -> None:
    org_id = uuid.uuid4()

    with patch.object(organizations_router, "AdminOrgService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.delete_organization.return_value = None
        MockService.return_value = mock_svc

        response = client.delete(f"/admin/organizations/{org_id}")

    assert response.status_code == status.HTTP_204_NO_CONTENT


@pytest.mark.anyio
async def test_update_organization_tier_success(
    client: TestClient, test_admin_role: Role
) -> None:
    org_id = uuid.uuid4()
    result = TierChangeResult(
        previous_tier=Tier.STARTER,
        new_tier=Tier.ENTERPRISE,
        worker_pool_provisioned=True,
        worker_pool_deprovisioned=False,
        error=None,
    )

    with patch.object(organizations_router, "AdminOrgService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.update_organization_tier.return_value = result
        MockService.return_value = mock_svc

        response = client.put(
            f"/admin/organizations/{org_id}/tier",
            json={"tier": "enterprise"},
        )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["previous_tier"] == "starter"
    assert data["new_tier"] == "enterprise"
    assert data["worker_pool_provisioned"] is True
    assert data["error"] is None


@pytest.mark.anyio
async def test_update_organization_tier_not_found(
    client: TestClient, test_admin_role: Role
) -> None:
    org_id = uuid.uuid4()

    with patch.object(organizations_router, "AdminOrgService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.update_organization_tier.side_effect = ValueError("not found")
        MockService.return_value = mock_svc

        response = client.put(
            f"/admin/organizations/{org_id}/tier",
            json={"tier": "pro"},
        )

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_update_organization_tier_with_error(
    client: TestClient, test_admin_role: Role
) -> None:
    """Test that tier change succeeds even if worker pool provisioning fails."""
    org_id = uuid.uuid4()
    result = TierChangeResult(
        previous_tier=Tier.STARTER,
        new_tier=Tier.ENTERPRISE,
        worker_pool_provisioned=False,
        worker_pool_deprovisioned=False,
        error="K8s API unavailable",
    )

    with patch.object(organizations_router, "AdminOrgService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.update_organization_tier.return_value = result
        MockService.return_value = mock_svc

        response = client.put(
            f"/admin/organizations/{org_id}/tier",
            json={"tier": "enterprise"},
        )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["new_tier"] == "enterprise"
    assert data["worker_pool_provisioned"] is False
    assert data["error"] == "K8s API unavailable"


@pytest.mark.anyio
async def test_create_organization_with_tier(
    client: TestClient, test_admin_role: Role
) -> None:
    org = _org_read(tier=Tier.PRO)

    with patch.object(organizations_router, "AdminOrgService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.create_organization.return_value = org
        MockService.return_value = mock_svc

        response = client.post(
            "/admin/organizations",
            json={"name": "Test Org", "slug": "test-org", "tier": "pro"},
        )

    assert response.status_code == status.HTTP_201_CREATED
    assert response.json()["tier"] == "pro"
