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
)

from tracecat import config
from tracecat.auth.types import Role
from tracecat.exceptions import TracecatValidationError
from tracecat.invitations.enums import InvitationStatus
from tracecat.pagination import CursorPaginatedResponse


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


_ORG_ID = uuid.uuid4()
_DOMAIN_ID = uuid.uuid4()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "path", "payload", "service_method", "error", "status_code"),
    [
        pytest.param(
            "GET",
            f"/admin/organizations/{_ORG_ID}",
            None,
            "get_organization",
            ValueError("not found"),
            status.HTTP_404_NOT_FOUND,
            id="get-organization-not-found",
        ),
        pytest.param(
            "PATCH",
            f"/admin/organizations/{_ORG_ID}",
            {"slug": "test-org"},
            "update_organization",
            ValueError("slug already exists"),
            status.HTTP_409_CONFLICT,
            id="update-organization-conflict",
        ),
        pytest.param(
            "POST",
            f"/admin/organizations/{_ORG_ID}/domains",
            {"domain": "example.com"},
            "create_org_domain",
            ValueError(
                "Domain 'example.com' is already assigned to another organization"
            ),
            status.HTTP_409_CONFLICT,
            id="create-domain-conflict",
        ),
        pytest.param(
            "POST",
            f"/admin/organizations/{_ORG_ID}/domains",
            {"domain": "example.com"},
            "create_org_domain",
            ValueError(f"Organization {_ORG_ID} not found"),
            status.HTTP_404_NOT_FOUND,
            id="create-domain-org-not-found",
        ),
        pytest.param(
            "PATCH",
            f"/admin/organizations/{_ORG_ID}/domains/{_DOMAIN_ID}",
            {"is_primary": True},
            "update_org_domain",
            ValueError("Domain not found"),
            status.HTTP_404_NOT_FOUND,
            id="update-domain-not-found",
        ),
        pytest.param(
            "POST",
            f"/admin/organizations/{_ORG_ID}/invitations",
            {"email": "owner@example.com"},
            "create_organization_invitation",
            TracecatValidationError(
                "An invitation already exists for owner@example.com in this organization"
            ),
            status.HTTP_400_BAD_REQUEST,
            id="invitation-duplicate",
        ),
    ],
)
async def test_admin_org_errors_map_to_http_status(
    client: TestClient,
    test_admin_role: Role,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
    payload: dict[str, object] | None,
    service_method: str,
    error: Exception,
    status_code: int,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", True)

    with patch.object(organizations_router, "AdminOrgService") as MockService:
        mock_svc = AsyncMock()
        getattr(mock_svc, service_method).side_effect = error
        MockService.return_value = mock_svc

        response = client.request(method, path, json=payload)

    assert response.status_code == status_code


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
async def test_create_organization_invitation_allowed_without_multi_tenant(
    client: TestClient,
    test_admin_role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id = uuid.uuid4()
    invitation = _org_invitation_read(org_id, token="raw-token")
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", False)

    with patch.object(organizations_router, "AdminOrgService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.create_organization_invitation.return_value = invitation
        MockService.return_value = mock_svc

        response = client.post(
            f"/admin/organizations/{org_id}/invitations",
            json={"email": "owner@example.com"},
        )

    assert response.status_code == status.HTTP_201_CREATED
    assert response.json()["token"] == "raw-token"


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
async def test_list_organization_invitations_allowed_without_multi_tenant(
    client: TestClient,
    test_admin_role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id = uuid.uuid4()
    invitation = _org_invitation_read(org_id)
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", False)

    with patch.object(organizations_router, "AdminOrgService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.list_organization_invitations.return_value = CursorPaginatedResponse[
            AdminOrgInvitationRead
        ](items=[invitation])
        MockService.return_value = mock_svc

        response = client.get(f"/admin/organizations/{org_id}/invitations")

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["items"][0]["id"] == str(invitation.id)


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
async def test_get_organization_invitation_token_allowed_without_multi_tenant(
    client: TestClient,
    test_admin_role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id = uuid.uuid4()
    invitation_id = uuid.uuid4()
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", False)

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
async def test_revoke_organization_invitation_allowed_without_multi_tenant(
    client: TestClient,
    test_admin_role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id = uuid.uuid4()
    invitation_id = uuid.uuid4()
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", False)

    with patch.object(organizations_router, "AdminOrgService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.revoke_organization_invitation.return_value = None
        MockService.return_value = mock_svc

        response = client.delete(
            f"/admin/organizations/{org_id}/invitations/{invitation_id}"
        )

    assert response.status_code == status.HTTP_204_NO_CONTENT
