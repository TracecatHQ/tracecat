"""HTTP-level tests for auth discovery endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

import tracecat.auth.discovery as auth_discovery_module
from tracecat.auth.discovery import AuthDiscoverResponse, AuthDiscoveryMethod


@pytest.mark.anyio
async def test_auth_discovery_returns_routing_hint(client: TestClient) -> None:
    with patch.object(auth_discovery_module, "AuthDiscoveryService") as mock_service:
        service = AsyncMock()
        service.discover.return_value = AuthDiscoverResponse(
            method=AuthDiscoveryMethod.SAML
        )
        mock_service.return_value = service

        response = client.post("/auth/discover", json={"email": "user@acme.com"})

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["method"] == AuthDiscoveryMethod.SAML.value


@pytest.mark.anyio
async def test_auth_discovery_returns_validation_error_for_invalid_email(
    client: TestClient,
) -> None:
    response = client.post("/auth/discover", json={"email": "not-an-email"})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
