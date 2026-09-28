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

pytest.mark.disable_fixture("test_user")


@pytest.mark.anyio
async def test_authenticated_service_client(mock_user_id, mock_org_id):
    service_role = Role(
        type="service", user_id=mock_user_id, service_id="tracecat-runner"
    )
    async with AuthenticatedServiceClient(role=service_role) as client:
        assert isinstance(client, AsyncClient)
        assert client.role == service_role
        assert client.headers["x-tracecat-role-service-id"] == "tracecat-runner"
        assert (
            client.headers["x-tracecat-service-key"]
            == os.environ["TRACECAT__SERVICE_KEY"]
        )
        assert uuid.UUID(client.headers["x-tracecat-role-user-id"]) == mock_user_id
        assert "x-tracecat-role-workspace-id" not in client.headers

    service_role = Role(type="service", service_id="tracecat-runner")
    async with AuthenticatedServiceClient(role=service_role) as client:
        assert isinstance(client, AsyncClient)
        assert client.role == service_role
        assert client.headers["x-tracecat-role-service-id"] == "tracecat-runner"
        assert (
            client.headers["x-tracecat-service-key"]
            == os.environ["TRACECAT__SERVICE_KEY"]
        )
        assert "x-tracecat-role-user-id" not in client.headers
        assert "x-tracecat-role-workspace-id" not in client.headers

    service_role = Role(
        type="service",
        service_id="tracecat-runner",
        workspace_id=mock_org_id,
        user_id=mock_user_id,
    )
    async with AuthenticatedServiceClient(role=service_role) as client:
        assert isinstance(client, AsyncClient)
        assert client.role == service_role
        assert client.headers["x-tracecat-role-service-id"] == "tracecat-runner"
        assert (
            client.headers["x-tracecat-service-key"]
            == os.environ["TRACECAT__SERVICE_KEY"]
        )
        assert uuid.UUID(client.headers["x-tracecat-role-user-id"]) == mock_user_id
        assert uuid.UUID(client.headers["x-tracecat-role-workspace-id"]) == mock_org_id


@pytest.mark.anyio
async def test_authenticated_service_client_init_with_role(mock_user_id):
    # Test initialization of AuthenticatedServiceClient
    role = Role(type="service", user_id=mock_user_id, service_id="tracecat-runner")
    async with AuthenticatedServiceClient(role=role) as client:
        assert isinstance(client, AsyncClient)
        assert client.role == role
        assert client.headers["x-tracecat-role-service-id"] == "tracecat-runner"
        assert (
            client.headers["x-tracecat-service-key"]
            == os.environ["TRACECAT__SERVICE_KEY"]
        )
        assert uuid.UUID(client.headers["x-tracecat-role-user-id"]) == mock_user_id
        assert "x-tracecat-role-workspace-id" not in client.headers


@pytest.mark.anyio
async def test_authenticated_service_client_init_no_role():
    """Test initialization of AuthenticatedServiceClient without role

    Expect:
    - role is the default role
    - headers are set with the default role
    - no user id in the headers
    """

    default_role = Role(type="service", service_id="tracecat-service")
    async with AuthenticatedServiceClient() as client:
        assert isinstance(client, AsyncClient)
        assert client.role == default_role
        assert client.headers["x-tracecat-role-service-id"] == "tracecat-service"
        assert (
            client.headers["x-tracecat-service-key"]
            == os.environ["TRACECAT__SERVICE_KEY"]
        )
        assert "x-tracecat-role-user-id" not in client.headers


@pytest.mark.anyio
async def test_authenticated_service_client_init_role_from_context(mock_user_id):
    # Test initialization of AuthenticatedServiceClient without role
    mock_ctx_role = Role(
        type="service",
        user_id=mock_user_id,
        service_id="tracecat-service",
    )
    token = ctx_role.set(mock_ctx_role)
    try:
        async with AuthenticatedServiceClient() as client:
            assert client.role == mock_ctx_role
            assert client.headers["x-tracecat-role-service-id"] == "tracecat-service"
            assert (
                client.headers["x-tracecat-service-key"]
                == os.environ["TRACECAT__SERVICE_KEY"]
            )
            assert uuid.UUID(client.headers["x-tracecat-role-user-id"]) == mock_user_id
    finally:
        ctx_role.reset(token)


@pytest.mark.anyio
async def test_authenticated_api_client_init_role_from_context(mock_user_id):
    # Test initialization of AuthenticatedAPIClient without role
    mock_ctx_role = Role(
        type="service",
        user_id=mock_user_id,
        service_id="tracecat-service",
    )
    token = ctx_role.set(mock_ctx_role)
    try:
        async with AuthenticatedAPIClient() as client:
            assert client.role == mock_ctx_role
            assert client.headers["x-tracecat-role-service-id"] == "tracecat-service"
            assert (
                client.headers["x-tracecat-service-key"]
                == os.environ["TRACECAT__SERVICE_KEY"]
            )
            assert uuid.UUID(client.headers["x-tracecat-role-user-id"]) == mock_user_id
            assert client.base_url == config.TRACECAT__API_URL
    finally:
        ctx_role.reset(token)


@pytest.mark.anyio
async def test_authenticated_api_client_init_no_role():
    # Test initialization of AuthenticatedAPIClient without role
    default_role = Role(type="service", service_id="tracecat-service")
    async with AuthenticatedAPIClient() as client:
        assert client.role == default_role
        assert client.headers["x-tracecat-role-service-id"] == "tracecat-service"
        assert (
            client.headers["x-tracecat-service-key"]
            == os.environ["TRACECAT__SERVICE_KEY"]
        )
        assert "x-tracecat-role-user-id" not in client.headers
        assert client.base_url == config.TRACECAT__API_URL


@pytest.mark.anyio
async def test_authenticated_api_client_init_with_role(mock_user_id, mock_org_id):
    # Test initialization of AuthenticatedAPIClient
    role = Role(
        type="service",
        user_id=mock_user_id,
        service_id="tracecat-runner",
        workspace_id=mock_org_id,
    )
    async with AuthenticatedAPIClient(role=role) as client:
        assert isinstance(client, AsyncClient)
        assert client.role == role
        assert client.headers["x-tracecat-role-service-id"] == "tracecat-runner"
        assert (
            client.headers["x-tracecat-service-key"]
            == os.environ["TRACECAT__SERVICE_KEY"]
        )
        assert uuid.UUID(client.headers["x-tracecat-role-user-id"]) == mock_user_id
        assert uuid.UUID(client.headers["x-tracecat-role-workspace-id"]) == mock_org_id
        assert client.base_url == config.TRACECAT__API_URL

    role = Role(type="service", service_id="tracecat-runner", workspace_id=mock_org_id)
    async with AuthenticatedAPIClient(role=role) as client:
        assert client.role == role
        assert client.headers["x-tracecat-role-service-id"] == "tracecat-runner"
        assert (
            client.headers["x-tracecat-service-key"]
            == os.environ["TRACECAT__SERVICE_KEY"]
        )
        assert "x-tracecat-role-user-id" not in client.headers
        assert uuid.UUID(client.headers["x-tracecat-role-workspace-id"]) == mock_org_id
        assert client.base_url == config.TRACECAT__API_URL


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
