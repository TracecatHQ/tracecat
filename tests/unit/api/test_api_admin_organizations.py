"""HTTP-level tests for admin organizations API endpoints."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from tracecat_ee.admin.organizations import router as organizations_router
from tracecat_ee.admin.organizations.schemas import OrgDomainRead, OrgRead

from tracecat.auth.types import Role


def _org_read(org_id: uuid.UUID | None = None) -> OrgRead:
    now = datetime(2024, 1, 1, tzinfo=UTC)
    return OrgRead(
        id=org_id or uuid.uuid4(),
        name="Test Org",
        slug="test-org",
        is_active=True,
        created_at=now,
        updated_at=now,
    )


def _org_domain_read(
    org_id: uuid.UUID,
    domain_id: uuid.UUID | None = None,
) -> OrgDomainRead:
    now = datetime(2024, 1, 1, tzinfo=UTC)
    return OrgDomainRead(
        id=domain_id or uuid.uuid4(),
        organization_id=org_id,
        domain="example.com",
        normalized_domain="example.com",
        is_primary=True,
        is_active=True,
        verified_at=None,
        verification_method="platform_admin",
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
    client: TestClient, test_admin_role: Role, monkeypatch: pytest.MonkeyPatch
) -> None:
    org = _org_read()
    monkeypatch.setattr(organizations_router.config, "TRACECAT__EE_MULTI_TENANT", True)

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
    client: TestClient, test_admin_role: Role, monkeypatch: pytest.MonkeyPatch
) -> None:
    org_id = uuid.uuid4()
    monkeypatch.setattr(organizations_router.config, "TRACECAT__EE_MULTI_TENANT", True)

    with patch.object(organizations_router, "AdminOrgService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.delete_organization.return_value = None
        MockService.return_value = mock_svc

        response = client.delete(f"/admin/organizations/{org_id}")

    assert response.status_code == status.HTTP_204_NO_CONTENT


@pytest.mark.anyio
async def test_create_organization_blocked_without_multi_tenant(
    client: TestClient, test_admin_role: Role, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(organizations_router.config, "TRACECAT__EE_MULTI_TENANT", False)

    response = client.post(
        "/admin/organizations",
        json={"name": "Test Org", "slug": "test-org"},
    )

    assert response.status_code == status.HTTP_402_PAYMENT_REQUIRED


@pytest.mark.anyio
async def test_delete_organization_blocked_without_multi_tenant(
    client: TestClient, test_admin_role: Role, monkeypatch: pytest.MonkeyPatch
) -> None:
    org_id = uuid.uuid4()
    monkeypatch.setattr(organizations_router.config, "TRACECAT__EE_MULTI_TENANT", False)

    response = client.delete(f"/admin/organizations/{org_id}")

    assert response.status_code == status.HTTP_402_PAYMENT_REQUIRED


@pytest.mark.anyio
async def test_list_org_domains_success(
    client: TestClient, test_admin_role: Role
) -> None:
    org_id = uuid.uuid4()
    domain = _org_domain_read(org_id)

    with patch.object(organizations_router, "AdminOrgService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.list_org_domains.return_value = [domain]
        MockService.return_value = mock_svc

        response = client.get(f"/admin/organizations/{org_id}/domains")

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data[0]["id"] == str(domain.id)
    assert data[0]["organization_id"] == str(org_id)


@pytest.mark.anyio
async def test_create_org_domain_conflict(
    client: TestClient, test_admin_role: Role
) -> None:
    org_id = uuid.uuid4()

    with patch.object(organizations_router, "AdminOrgService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.create_org_domain.side_effect = ValueError(
            "Domain 'example.com' is already assigned to another organization"
        )
        MockService.return_value = mock_svc

        response = client.post(
            f"/admin/organizations/{org_id}/domains",
            json={"domain": "example.com"},
        )

    assert response.status_code == status.HTTP_409_CONFLICT


@pytest.mark.anyio
async def test_create_org_domain_not_found(
    client: TestClient, test_admin_role: Role
) -> None:
    org_id = uuid.uuid4()

    with patch.object(organizations_router, "AdminOrgService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.create_org_domain.side_effect = ValueError(
            f"Organization {org_id} not found"
        )
        MockService.return_value = mock_svc

        response = client.post(
            f"/admin/organizations/{org_id}/domains",
            json={"domain": "example.com"},
        )

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_update_org_domain_not_found(
    client: TestClient, test_admin_role: Role
) -> None:
    org_id = uuid.uuid4()
    domain_id = uuid.uuid4()

    with patch.object(organizations_router, "AdminOrgService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.update_org_domain.side_effect = ValueError("Domain not found")
        MockService.return_value = mock_svc

        response = client.patch(
            f"/admin/organizations/{org_id}/domains/{domain_id}",
            json={"is_primary": True},
        )

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_delete_org_domain_success(
    client: TestClient, test_admin_role: Role
) -> None:
    org_id = uuid.uuid4()
    domain_id = uuid.uuid4()

    with patch.object(organizations_router, "AdminOrgService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.delete_org_domain.return_value = None
        MockService.return_value = mock_svc

        response = client.delete(f"/admin/organizations/{org_id}/domains/{domain_id}")

    assert response.status_code == status.HTTP_204_NO_CONTENT
