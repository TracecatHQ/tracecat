import uuid

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from tracecat.auth.credentials import RoleACL
from tracecat.logger import logger
from tracecat.types.auth import AccessLevel, Role

WORKSPACE_ID = uuid.uuid4()
USER_ID = uuid.uuid4()
SERVICE_ID = "tracecat-api"


router = APIRouter()


@router.get("/test")
async def test(role: Role = RoleACL(allow_user=True, allow_service=True)):
    return {"test": "test", "role": role.model_dump()}


@pytest.fixture
def test_client(test_role):
    """Create a FastAPI test client with our router and mocked RoleACL."""
    app = FastAPI()

    app.include_router(router)
    return TestClient(app)


def test_endpoint_can_be_hit(test_client: TestClient):
    """
    Test that the /test endpoint is accessible and returns the expected response.

    Args:
        test_client: Pytest fixture that provides a configured TestClient
    """
    response = test_client.get("/test")

    assert response.status_code == 200
    assert response.json() == {
        "test": "test",
        "role": {
            "type": "user",
            "access_level": 0,
            "workspace_id": str(WORKSPACE_ID),
            "user_id": str(USER_ID),
            "service_id": SERVICE_ID,
        },
    }


@pytest.fixture
def override_test_role():
    """
    Fixture that creates a different test role for dependency override testing.


        Role: A test role with modified values
    """

    async def _override_test_role(*args, **kwargs):
        return Role(
            type="service",
            workspace_id=WORKSPACE_ID,
            user_id=USER_ID,
            service_id=SERVICE_ID,
            access_level=AccessLevel.ADMIN,
        )

    return _override_test_role


@pytest.fixture
def test_client_with_override(
    monkeypatch: pytest.MonkeyPatch, override_test_role: Role
):
    """
    Create a FastAPI test client with our router and overridden role dependency.

    Args:
        override_test_role: Pytest fixture providing the override role

    Returns:
        TestClient: Configured test client with dependency override
    """

    app = FastAPI()
    app.include_router(router)

    # Override the role dependency with our custom role
    monkeypatch.setattr(
        "tracecat.auth.credentials._role_dependency",
        override_test_role,
    )

    return TestClient(app)


def test_endpoint_access_with_override(test_client_with_override: TestClient):
    """
    Test that the /test endpoint uses the overridden role dependency.

    Args:
        test_client_with_override: Pytest fixture that provides a TestClient with overridden dependencies
    """
    response = test_client_with_override.get(
        "/test", params={"workspace_id": str(WORKSPACE_ID)}
    )
    details = response.json()
    logger.info("RESPONSE", response=response, details=details)

    assert response.status_code == 200
    assert response.json() == {
        "test": "test",
        "role": {
            "type": "service",
            "access_level": AccessLevel.ADMIN,
            "workspace_id": str(WORKSPACE_ID),
            "user_id": str(USER_ID),
            "service_id": SERVICE_ID,
        },
    }
