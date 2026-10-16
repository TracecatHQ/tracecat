import importlib
import os
import uuid

import pytest
from httpx import AsyncClient

from tracecat import config
from tracecat.auth import users as users_mod
from tracecat.auth.types import Role
from tracecat.clients import AuthenticatedAPIClient, AuthenticatedServiceClient
from tracecat.contexts import ctx_role
from tracecat.identifiers import InternalServiceID


@pytest.mark.anyio
@pytest.mark.parametrize(
    "client_cls", [AuthenticatedServiceClient, AuthenticatedAPIClient]
)
@pytest.mark.parametrize(
    ("source", "service_id", "with_user", "with_workspace"),
    [
        ("explicit", "tracecat-runner", True, False),
        ("explicit", "tracecat-runner", False, False),
        ("explicit", "tracecat-runner", True, True),
        ("explicit", "tracecat-runner", False, True),
        ("context", "tracecat-service", True, False),
        ("default", "tracecat-service", False, False),
    ],
)
async def test_authenticated_client_role_headers(
    client_cls: type[AuthenticatedServiceClient] | type[AuthenticatedAPIClient],
    source: str,
    service_id: InternalServiceID,
    with_user: bool,
    with_workspace: bool,
    mock_user_id: uuid.UUID,
    mock_org_id: uuid.UUID,
) -> None:
    """Role precedence is explicit > ctx_role > default, mirrored into headers."""
    role = Role(
        type="service",
        service_id=service_id,
        user_id=mock_user_id if with_user else None,
        workspace_id=mock_org_id if with_workspace else None,
    )
    token = ctx_role.set(role) if source == "context" else None
    try:
        async with client_cls(role=role if source == "explicit" else None) as client:
            assert isinstance(client, AsyncClient)
            assert client.role == role
            assert client.headers["x-tracecat-role-service-id"] == service_id
            assert (
                client.headers["x-tracecat-service-key"]
                == os.environ["TRACECAT__SERVICE_KEY"]
            )
            if with_user:
                assert (
                    uuid.UUID(client.headers["x-tracecat-role-user-id"]) == mock_user_id
                )
            else:
                assert "x-tracecat-role-user-id" not in client.headers
            if with_workspace:
                assert (
                    uuid.UUID(client.headers["x-tracecat-role-workspace-id"])
                    == mock_org_id
                )
            else:
                assert "x-tracecat-role-workspace-id" not in client.headers
            if client_cls is AuthenticatedAPIClient:
                assert client.base_url == config.TRACECAT__API_URL
    finally:
        if token is not None:
            ctx_role.reset(token)


@pytest.mark.parametrize(
    ("public_api_url", "expected"),
    [
        # Browser-facing URL terminates TLS at the load balancer, so the session
        # cookie must be marked Secure regardless of the internal API URL scheme.
        ("https://tracecat.example.com/api", True),
        ("http://localhost/api", False),
    ],
)
def test_cookie_secure_derived_from_public_api_url(
    monkeypatch, public_api_url, expected
):
    monkeypatch.setattr(config, "TRACECAT__PUBLIC_API_URL", public_api_url)
    reloaded = importlib.reload(users_mod)
    assert reloaded.cookie_transport.cookie_secure is expected
    # Ensure the internal API URL never gates the Secure flag on its own.
    monkeypatch.setattr(config, "TRACECAT__API_URL", "http://api-service:8000")
    reloaded = importlib.reload(users_mod)
    assert reloaded.cookie_transport.cookie_secure is expected
