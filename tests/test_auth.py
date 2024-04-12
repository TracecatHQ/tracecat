import os

import pytest
from httpx import AsyncClient

from tracecat.auth import (
    AuthenticatedAPIClient,
    AuthenticatedRunnerClient,
    AuthenticatedServiceClient,
    Role,
    decrypt,
    decrypt_object,
    encrypt,
    encrypt_object,
)
from tracecat.config import TRACECAT__API_URL, TRACECAT__RUNNER_URL
from tracecat.contexts import ctx_session_role


def test_encrypt_decrypt():
    api_key = "mock_api_key"
    encrypted_api_key = encrypt(api_key)
    decrypted_api_key = decrypt(encrypted_api_key)
    assert decrypted_api_key == api_key


def test_encrypt_decrypt_object():
    obj = {
        "client_id": "TEST_CLIENT_ID",
        "client_secret": "TEST_CLIENT_SECRET",
        "metadata": {"value": 1},
    }
    encrypted_obj = encrypt_object(obj)
    decrypted_obj = decrypt_object(encrypted_obj)
    assert decrypted_obj == obj


@pytest.mark.asyncio
async def test_authenticated_service_client():
    service_role = Role(
        type="service", user_id="mock_user_id", service_id="mock_service_id"
    )
    async with AuthenticatedServiceClient(role=service_role) as client:
        assert isinstance(client, AsyncClient)
        assert client.role == service_role
        assert client.headers["Service-Role"] == "mock_service_id"
        assert client.headers["X-API-Key"] == os.environ["TRACECAT__SERVICE_KEY"]
        assert client.headers["Service-User-ID"] == "mock_user_id"

    service_role = Role(type="service", service_id="mock_service_id")
    async with AuthenticatedServiceClient(role=service_role) as client:
        assert isinstance(client, AsyncClient)
        assert client.role == service_role
        assert client.headers["Service-Role"] == "mock_service_id"
        assert client.headers["X-API-Key"] == os.environ["TRACECAT__SERVICE_KEY"]
        assert "Service-User-ID" not in client.headers

    service_role = Role(type="service")
    async with AuthenticatedServiceClient(role=service_role) as client:
        assert isinstance(client, AsyncClient)
        assert client.role == service_role
        assert client.headers["Service-Role"] == "tracecat-service"
        assert client.headers["X-API-Key"] == os.environ["TRACECAT__SERVICE_KEY"]
        assert "Service-User-ID" not in client.headers


@pytest.mark.asyncio
async def test_authenticated_service_client_init_with_role():
    # Test initialization of AuthenticatedServiceClient
    role = Role(type="service", user_id="mock_user_id", service_id="mock_service_id")
    async with AuthenticatedServiceClient(role=role) as client:
        assert isinstance(client, AsyncClient)
        assert client.role == role
        assert client.headers["Service-Role"] == "mock_service_id"
        assert client.headers["X-API-Key"] == os.environ["TRACECAT__SERVICE_KEY"]
        assert client.headers["Service-User-ID"] == "mock_user_id"


@pytest.mark.asyncio
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
        assert client.headers["Service-Role"] == "tracecat-service"
        assert client.headers["X-API-Key"] == os.environ["TRACECAT__SERVICE_KEY"]
        assert "Service-User-ID" not in client.headers


@pytest.mark.asyncio
async def test_authenticated_service_client_init_role_from_context():
    # Test initialization of AuthenticatedServiceClient without role
    mock_ctx_role = Role(
        type="service",
        user_id="mock_ctx_user_id",
        service_id="mock_ctx_service_id",
    )
    ctx_session_role.set(mock_ctx_role)

    async with AuthenticatedServiceClient() as client:
        assert client.role == mock_ctx_role
        assert client.headers["Service-Role"] == "mock_ctx_service_id"
        assert client.headers["X-API-Key"] == os.environ["TRACECAT__SERVICE_KEY"]
        assert client.headers["Service-User-ID"] == "mock_ctx_user_id"


@pytest.mark.asyncio
async def test_authenticated_runner_client_init_no_role():
    # Test initialization of AuthenticatedAPIClient without role
    default_role = Role(type="service", service_id="tracecat-service")
    async with AuthenticatedRunnerClient() as client:
        assert client.role == default_role
        assert client.headers["Service-Role"] == "tracecat-service"
        assert client.headers["X-API-Key"] == os.environ["TRACECAT__SERVICE_KEY"]
        assert "Service-User-ID" not in client.headers
        assert client.base_url == TRACECAT__RUNNER_URL


@pytest.mark.asyncio
async def test_authenticated_runner_client_init_with_role():
    # Test initialization of AuthenticatedServiceClient
    role = Role(type="service", user_id="mock_user_id", service_id="mock_service_id")
    async with AuthenticatedRunnerClient(role=role) as client:
        assert isinstance(client, AsyncClient)
        assert client.role == role
        assert client.headers["Service-Role"] == "mock_service_id"
        assert client.headers["X-API-Key"] == os.environ["TRACECAT__SERVICE_KEY"]
        assert client.headers["Service-User-ID"] == "mock_user_id"
        assert client.base_url == TRACECAT__RUNNER_URL

    role = Role(type="service", service_id="mock_service_id")
    async with AuthenticatedRunnerClient(role=role) as client:
        assert client.role == role
        assert client.headers["Service-Role"] == "mock_service_id"
        assert client.headers["X-API-Key"] == os.environ["TRACECAT__SERVICE_KEY"]
        assert "Service-User-ID" not in client.headers
        assert client.base_url == TRACECAT__RUNNER_URL

    role = Role(type="service")
    async with AuthenticatedRunnerClient(role=role) as client:
        assert client.role == role
        assert client.headers["Service-Role"] == "tracecat-service"
        assert client.headers["X-API-Key"] == os.environ["TRACECAT__SERVICE_KEY"]
        assert "Service-User-ID" not in client.headers
        assert client.base_url == TRACECAT__RUNNER_URL

    role = Role(type="service", user_id="mock_user_id")
    async with AuthenticatedRunnerClient(role=role) as client:
        assert client.role == role
        assert client.headers["Service-Role"] == "tracecat-service"
        assert client.headers["X-API-Key"] == os.environ["TRACECAT__SERVICE_KEY"]
        assert client.headers["Service-User-ID"] == "mock_user_id"
        assert client.base_url == TRACECAT__RUNNER_URL


@pytest.mark.asyncio
async def test_authenticated_runner_client_init_role_from_context():
    # Test initialization of AuthenticatedAPIClient without role
    mock_ctx_role = Role(
        type="service",
        user_id="mock_ctx_user_id",
        service_id="mock_ctx_service_id",
    )
    ctx_session_role.set(mock_ctx_role)

    async with AuthenticatedRunnerClient() as client:
        assert client.role == mock_ctx_role
        assert client.headers["Service-Role"] == "mock_ctx_service_id"
        assert client.headers["X-API-Key"] == os.environ["TRACECAT__SERVICE_KEY"]
        assert client.headers["Service-User-ID"] == "mock_ctx_user_id"
        assert client.base_url == TRACECAT__RUNNER_URL


@pytest.mark.asyncio
async def test_authenticated_api_client_init_no_role():
    # Test initialization of AuthenticatedAPIClient without role
    default_role = Role(type="service", service_id="tracecat-service")
    async with AuthenticatedAPIClient() as client:
        assert client.role == default_role
        assert client.headers["Service-Role"] == "tracecat-service"
        assert client.headers["X-API-Key"] == os.environ["TRACECAT__SERVICE_KEY"]
        assert "Service-User-ID" not in client.headers
        assert client.base_url == TRACECAT__API_URL


@pytest.mark.asyncio
async def test_authenticated_api_client_init_with_role():
    # Test initialization of AuthenticatedAPIClient
    role = Role(type="service", user_id="mock_user_id", service_id="mock_service_id")
    async with AuthenticatedAPIClient(role=role) as client:
        assert isinstance(client, AsyncClient)
        assert client.role == role
        assert client.headers["Service-Role"] == "mock_service_id"
        assert client.headers["X-API-Key"] == os.environ["TRACECAT__SERVICE_KEY"]
        assert client.headers["Service-User-ID"] == "mock_user_id"
        assert client.base_url == TRACECAT__API_URL

    role = Role(type="service", service_id="mock_service_id")
    async with AuthenticatedAPIClient(role=role) as client:
        assert client.role == role
        assert client.headers["Service-Role"] == "mock_service_id"
        assert client.headers["X-API-Key"] == os.environ["TRACECAT__SERVICE_KEY"]
        assert "Service-User-ID" not in client.headers
        assert client.base_url == TRACECAT__API_URL

    role = Role(type="service")
    async with AuthenticatedAPIClient(role=role) as client:
        assert client.role == role
        assert client.headers["Service-Role"] == "tracecat-service"
        assert client.headers["X-API-Key"] == os.environ["TRACECAT__SERVICE_KEY"]
        assert "Service-User-ID" not in client.headers
        assert client.base_url == TRACECAT__API_URL

    role = Role(type="service", user_id="mock_user_id")
    async with AuthenticatedAPIClient(role=role) as client:
        assert client.role == role
        assert client.headers["Service-Role"] == "tracecat-service"
        assert client.headers["X-API-Key"] == os.environ["TRACECAT__SERVICE_KEY"]
        assert client.headers["Service-User-ID"] == "mock_user_id"
        assert client.base_url == TRACECAT__API_URL
