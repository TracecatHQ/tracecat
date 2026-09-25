"""HTTP-level tests for the SCIM connection token management routes."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from tracecat_ee.scim.connections import IssuedScimConnection

from tracecat.auth.api_keys import SCIM_API_KEY_PREFIX
from tracecat.auth.types import Role
from tracecat.exceptions import TracecatNotFoundError

BASE = "/scim/connection"


def _connection(org_id: uuid.UUID) -> SimpleNamespace:
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=org_id,
        preview=f"{SCIM_API_KEY_PREFIX}...abcd",
        last_used_at=None,
        revoked_at=None,
        status="pending",
        created_at=now,
        updated_at=now,
    )


@pytest.mark.anyio
async def test_issue_returns_the_raw_token_once(
    client: TestClient, test_admin_role: Role
) -> None:
    org_id = test_admin_role.organization_id
    assert org_id is not None
    raw = f"{SCIM_API_KEY_PREFIX}{uuid.uuid4().hex[:12]}_{uuid.uuid4().hex}"
    issued = IssuedScimConnection(connection=_connection(org_id), token=raw)  # pyright: ignore[reportArgumentType]

    with (
        patch(
            "tracecat_ee.scim.router.check_entitlement",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "tracecat_ee.scim.connections.ScimConnectionService.issue_token",
            new=AsyncMock(return_value=issued),
        ),
    ):
        response = client.post(BASE)

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["token"] == raw
    assert body["connection"]["preview"].startswith(SCIM_API_KEY_PREFIX)


@pytest.mark.anyio
async def test_get_never_returns_the_token(
    client: TestClient, test_admin_role: Role
) -> None:
    org_id = test_admin_role.organization_id
    assert org_id is not None
    connection = _connection(org_id)

    with (
        patch(
            "tracecat_ee.scim.router.check_entitlement",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "tracecat_ee.scim.connections.ScimConnectionService.get_connection",
            new=AsyncMock(return_value=connection),
        ),
    ):
        response = client.get(BASE)

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert "token" not in body
    assert body["preview"] == connection.preview
    assert body["revoked_at"] is None
    assert body["status"] == "pending"


@pytest.mark.anyio
async def test_get_without_a_connection_is_404(
    client: TestClient, test_admin_role: Role
) -> None:
    with (
        patch(
            "tracecat_ee.scim.router.check_entitlement",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "tracecat_ee.scim.connections.ScimConnectionService.get_connection",
            new=AsyncMock(side_effect=TracecatNotFoundError("missing")),
        ),
    ):
        response = client.get(BASE)

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_disconnect_returns_no_content(
    client: TestClient, test_admin_role: Role
) -> None:
    with (
        patch(
            "tracecat_ee.scim.router.check_entitlement",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "tracecat_ee.scim.service.SCIMService.disconnect",
            new=AsyncMock(return_value=None),
        ),
    ):
        response = client.post("/scim/disconnect")

    assert response.status_code == status.HTTP_204_NO_CONTENT


@pytest.mark.anyio
async def test_disconnect_without_a_connection_is_404(
    client: TestClient, test_admin_role: Role
) -> None:
    with (
        patch(
            "tracecat_ee.scim.router.check_entitlement",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "tracecat_ee.scim.service.SCIMService.disconnect",
            new=AsyncMock(side_effect=TracecatNotFoundError("missing")),
        ),
    ):
        response = client.post("/scim/disconnect")

    assert response.status_code == status.HTTP_404_NOT_FOUND
