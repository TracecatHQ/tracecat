"""HTTP-level tests for SAML login routing and gating."""

from __future__ import annotations

import uuid
from typing import cast
from unittest.mock import ANY, AsyncMock, Mock, patch

import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient

import tracecat.auth.saml as saml_module
from tracecat.api.common import bootstrap_role
from tracecat.auth.enums import AuthType


def _override_saml_db_session(client: TestClient) -> Mock:
    db_session = Mock()
    db_session.add = Mock()
    db_session.commit = AsyncMock()

    async def override_get_async_session() -> Mock:
        return db_session

    app = cast(FastAPI, client.app)
    app.dependency_overrides[saml_module.get_async_session] = override_get_async_session
    return db_session


@pytest.mark.anyio
async def test_saml_login_uses_resolved_org_for_auth_gate(
    client: TestClient,
) -> None:
    organization_id = uuid.uuid4()
    _override_saml_db_session(client)
    saml_client = Mock()
    saml_client.prepare_for_authenticate.return_value = (
        "req-123",
        {"headers": [("Location", "https://idp.example.com/sso")]},
    )

    with (
        patch.object(
            saml_module,
            "resolve_auth_organization_id",
            AsyncMock(return_value=organization_id),
        ) as resolve_org_mock,
        patch.object(
            saml_module,
            "verify_auth_type",
            AsyncMock(),
        ) as verify_auth_type_mock,
        patch.object(
            saml_module,
            "get_org_saml_metadata_url",
            AsyncMock(return_value="https://metadata.example.com"),
        ),
        patch.object(
            saml_module,
            "create_saml_client",
            AsyncMock(return_value=saml_client),
        ),
    ):
        response = client.get("/auth/saml/login?org=example-org")

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"redirect_url": "https://idp.example.com/sso"}
    resolve_org_mock.assert_awaited_once()
    verify_auth_type_mock.assert_awaited_once_with(
        AuthType.SAML,
        role=bootstrap_role(organization_id),
        session=ANY,
    )


@pytest.mark.anyio
async def test_saml_login_stops_before_handler_when_org_scoped_gate_fails(
    client: TestClient,
) -> None:
    organization_id = uuid.uuid4()
    _override_saml_db_session(client)

    with (
        patch.object(
            saml_module,
            "resolve_auth_organization_id",
            AsyncMock(return_value=organization_id),
        ),
        patch.object(
            saml_module,
            "verify_auth_type",
            AsyncMock(
                side_effect=saml_module.HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Auth type saml is not enabled",
                )
            ),
        ),
        patch.object(
            saml_module,
            "get_org_saml_metadata_url",
            AsyncMock(),
        ) as get_metadata_mock,
    ):
        response = client.get("/auth/saml/login?org=example-org")

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["detail"] == "Auth type saml is not enabled"
    get_metadata_mock.assert_not_awaited()
