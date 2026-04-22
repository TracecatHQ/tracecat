"""HTTP-level tests for admin organizations API endpoints."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from tracecat_ee.admin.organizations import router as organizations_router
from tracecat_ee.admin.organizations.schemas import (
    AdminOrgInvitationCreateResponse,
    AdminOrgInvitationRead,
    AdminOrgInvitationTokenRead,
    OrgDomainRead,
    OrgRead,
)

from tracecat import config
from tracecat.auth.types import Role
from tracecat.exceptions import TracecatValidationError
from tracecat.invitations.enums import InvitationStatus
from tracecat.pagination import CursorPaginatedResponse, CursorPaginationParams


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


def _org_invitation_read(
    org_id: uuid.UUID,
    invitation_id: uuid.UUID | None = None,
    *,
    token: str | None = None,
) -> AdminOrgInvitationRead | AdminOrgInvitationCreateResponse:
    now = datetime(2024, 1, 1, tzinfo=UTC)
    invitation_data = {
        "id": invitation_id or uuid.uuid4(),
        "organization_id": org_id,
        "email": "owner@example.com",
        "role_id": uuid.uuid4(),
        "role_name": "Organization Owner",
        "role_slug": "organization-owner",
        "status": InvitationStatus.PENDING,
        "invited_by": uuid.uuid4(),
        "expires_at": now,
        "created_at": now,
        "accepted_at": None,
        "created_by_platform_admin": True,
    }
    if token is not None:
        return AdminOrgInvitationCreateResponse(**invitation_data, token=token)
    return AdminOrgInvitationRead(**invitation_data)


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
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", True)

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
    org_name = "Test Org"
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", True)

    with patch.object(organizations_router, "AdminOrgService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.delete_organization.return_value = None
        MockService.return_value = mock_svc

        response = client.delete(f"/admin/organizations/{org_id}?confirm={org_name}")

    assert response.status_code == status.HTTP_204_NO_CONTENT
    mock_svc.delete_organization.assert_awaited_once_with(
        org_id,
        confirmation=org_name,
    )


@pytest.mark.anyio
async def test_delete_organization_bad_confirmation_returns_400(
    client: TestClient, test_admin_role: Role, monkeypatch: pytest.MonkeyPatch
) -> None:
    org_id = uuid.uuid4()
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", True)

    with patch.object(organizations_router, "AdminOrgService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.delete_organization.side_effect = TracecatValidationError(
            "Confirmation text must exactly match the organization name."
        )
        MockService.return_value = mock_svc

        response = client.delete(f"/admin/organizations/{org_id}?confirm=wrong")

    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.anyio
async def test_create_organization_blocked_without_multi_tenant(
    client: TestClient, test_admin_role: Role, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", False)

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
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", False)

    response = client.delete(f"/admin/organizations/{org_id}")

    assert response.status_code == status.HTTP_402_PAYMENT_REQUIRED


@pytest.mark.anyio
async def test_create_organization_invitation_success(
    client: TestClient,
    test_admin_role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id = uuid.uuid4()
    invitation = _org_invitation_read(org_id, token="raw-token")
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", True)

    with patch.object(organizations_router, "AdminOrgService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.create_organization_invitation.return_value = invitation
        MockService.return_value = mock_svc

        response = client.post(
            f"/admin/organizations/{org_id}/invitations",
            json={"email": "owner@example.com"},
        )

    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["token"] == "raw-token"
    assert data["role_slug"] == "organization-owner"
    args = mock_svc.create_organization_invitation.await_args.args
    assert args[0] == org_id
    assert args[1].role_slug == "organization-owner"


@pytest.mark.anyio
async def test_create_organization_invitation_rejects_invalid_role_slug(
    client: TestClient,
    test_admin_role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id = uuid.uuid4()
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", True)

    response = client.post(
        f"/admin/organizations/{org_id}/invitations",
        json={"email": "owner@example.com", "role_slug": "custom-admin"},
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


@pytest.mark.anyio
async def test_create_organization_invitation_duplicate_returns_400(
    client: TestClient,
    test_admin_role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id = uuid.uuid4()
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", True)

    with patch.object(organizations_router, "AdminOrgService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.create_organization_invitation.side_effect = TracecatValidationError(
            "An invitation already exists for owner@example.com in this organization"
        )
        MockService.return_value = mock_svc

        response = client.post(
            f"/admin/organizations/{org_id}/invitations",
            json={"email": "owner@example.com"},
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.anyio
async def test_list_organization_invitations_success(
    client: TestClient,
    test_admin_role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id = uuid.uuid4()
    invitation = _org_invitation_read(org_id)
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", True)

    with patch.object(organizations_router, "AdminOrgService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.list_organization_invitations.return_value = CursorPaginatedResponse[
            AdminOrgInvitationRead
        ](
            items=[invitation],
            next_cursor="next-cursor",
            has_more=True,
        )
        MockService.return_value = mock_svc

        response = client.get(
            f"/admin/organizations/{org_id}/invitations"
            "?status=pending&limit=25&cursor=current-cursor&reverse=true"
        )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["items"][0]["id"] == str(invitation.id)
    assert data["next_cursor"] == "next-cursor"
    assert data["has_more"] is True
    mock_svc.list_organization_invitations.assert_awaited_once_with(
        org_id,
        status=InvitationStatus.PENDING,
        pagination=CursorPaginationParams(
            limit=25,
            cursor="current-cursor",
            reverse=True,
        ),
    )


@pytest.mark.anyio
async def test_list_organization_invitations_invalid_cursor(
    client: TestClient,
    test_admin_role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id = uuid.uuid4()
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", True)

    with patch.object(organizations_router, "AdminOrgService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.list_organization_invitations.side_effect = TracecatValidationError(
            "Invalid cursor for organization invitations"
        )
        MockService.return_value = mock_svc

        response = client.get(
            f"/admin/organizations/{org_id}/invitations?cursor=not-a-cursor"
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.anyio
async def test_get_organization_invitation_token_success(
    client: TestClient,
    test_admin_role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id = uuid.uuid4()
    invitation_id = uuid.uuid4()
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", True)

    with patch.object(organizations_router, "AdminOrgService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.get_organization_invitation_token.return_value = (
            AdminOrgInvitationTokenRead(token="raw-token")
        )
        MockService.return_value = mock_svc

        response = client.get(
            f"/admin/organizations/{org_id}/invitations/{invitation_id}/token"
        )

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"token": "raw-token"}


@pytest.mark.anyio
async def test_revoke_organization_invitation_success(
    client: TestClient,
    test_admin_role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id = uuid.uuid4()
    invitation_id = uuid.uuid4()
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", True)

    with patch.object(organizations_router, "AdminOrgService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.revoke_organization_invitation.return_value = None
        MockService.return_value = mock_svc

        response = client.delete(
            f"/admin/organizations/{org_id}/invitations/{invitation_id}"
        )

    assert response.status_code == status.HTTP_204_NO_CONTENT
    mock_svc.revoke_organization_invitation.assert_awaited_once_with(
        org_id,
        invitation_id,
    )


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
