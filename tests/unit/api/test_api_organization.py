"""HTTP-level tests for the organization API endpoint.

Exercises ``GET /organization`` and verifies the response shape, including
the ``disable_github_workflow_pulls`` flag that workspace members rely on
to gate the workspace Git sync Pull UI.
"""

import uuid
from typing import get_args
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from tracecat.api.app import app
from tracecat.auth.types import Role
from tracecat.contexts import ctx_role
from tracecat.db.engine import get_async_session
from tracecat.organization import router as organization_router


def _override_role_dependency() -> Role:
    role = ctx_role.get()
    if role is None:
        raise RuntimeError("No role set in ctx_role context")
    return role


@pytest.fixture(autouse=True)
def _override_organization_role_dependencies(  # pyright: ignore[reportUnusedFunction]
    client: TestClient,
):
    role_dependencies = [
        organization_router.OrgActorRole,
        organization_router.OrgUserRole,
    ]

    for annotated_type in role_dependencies:
        metadata = get_args(annotated_type)
        if metadata and hasattr(metadata[1], "dependency"):
            dependency = metadata[1].dependency
            app.dependency_overrides[dependency] = _override_role_dependency

    yield

    for annotated_type in role_dependencies:
        metadata = get_args(annotated_type)
        if metadata and hasattr(metadata[1], "dependency"):
            dependency = metadata[1].dependency
            app.dependency_overrides.pop(dependency, None)


def _stub_organization(
    org_id: uuid.UUID,
    *,
    disable_github_workflow_pulls: bool = False,
) -> Mock:
    org = Mock()
    org.id = org_id
    org.name = "Test Organization"
    org.disable_github_workflow_pulls = disable_github_workflow_pulls
    return org


@pytest.mark.anyio
async def test_get_organization_exposes_disable_github_workflow_pulls_default(
    client: TestClient, test_admin_role: Role
) -> None:
    """A workspace member sees ``disable_github_workflow_pulls=False`` by default."""
    org = _stub_organization(test_admin_role.organization_id or uuid.uuid4())

    org_result = Mock()
    org_result.scalar_one_or_none.return_value = org

    mock_session = await app.dependency_overrides[get_async_session]()
    mock_session.execute = AsyncMock(return_value=org_result)

    response = client.get("/organization")

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()
    assert payload["id"] == str(org.id)
    assert payload["name"] == org.name
    assert payload["disable_github_workflow_pulls"] is False


@pytest.mark.anyio
async def test_get_organization_exposes_disable_github_workflow_pulls_when_set(
    client: TestClient, test_admin_role: Role
) -> None:
    """When the org admin enables the flag, workspace members see ``True``."""
    org = _stub_organization(
        test_admin_role.organization_id or uuid.uuid4(),
        disable_github_workflow_pulls=True,
    )

    org_result = Mock()
    org_result.scalar_one_or_none.return_value = org

    mock_session = await app.dependency_overrides[get_async_session]()
    mock_session.execute = AsyncMock(return_value=org_result)

    response = client.get("/organization")

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["disable_github_workflow_pulls"] is True


@pytest.mark.anyio
async def test_get_organization_returns_404_when_org_missing(
    client: TestClient, test_admin_role: Role
) -> None:
    """Bogus ``organization_id`` from the role context surfaces a 404."""
    org_result = Mock()
    org_result.scalar_one_or_none.return_value = None

    mock_session = await app.dependency_overrides[get_async_session]()
    mock_session.execute = AsyncMock(return_value=org_result)

    response = client.get("/organization")

    assert response.status_code == status.HTTP_404_NOT_FOUND
