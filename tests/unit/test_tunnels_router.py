"""API-level tests for workspace tunnels: entitlement gate and IDOR isolation."""

from collections.abc import AsyncGenerator
from typing import get_args
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_ee.tunnels import router as tunnels_router_module
from tracecat_ee.tunnels.router import router as tunnels_router
from tracecat_ee.tunnels.service import TunnelsService

from tracecat.auth.dependencies import WorkspaceActorRouteRole
from tracecat.auth.types import Role
from tracecat.db.engine import get_async_session
from tracecat.db.models import Organization, Workspace
from tracecat.exceptions import EntitlementRequired
from tracecat.tiers.enums import Entitlement

pytestmark = [pytest.mark.usefixtures("db"), pytest.mark.anyio]

SECRET = "machine-client-secret"
CREATE_BODY = {
    "name": "onprem-splunk",
    "endpoint": "https://pangolin.example.com",
    "client_id": "client-123",
    "client_secret": SECRET,
    "allowed_targets": [{"host": "splunk.corp.example", "ports": [8089]}],
}


class _RoleHolder:
    def __init__(self, role: Role) -> None:
        self.role = role


@pytest.fixture
async def other_workspace(
    session: AsyncSession, svc_organization: Organization
) -> AsyncGenerator[Workspace, None]:
    workspace = Workspace(name="other-workspace", organization_id=svc_organization.id)
    session.add(workspace)
    await session.commit()
    yield workspace


@pytest.fixture
def entitlement_check(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    check = AsyncMock(return_value=None)
    monkeypatch.setattr(tunnels_router_module, "check_entitlement", check)
    monkeypatch.setattr(TunnelsService, "has_entitlement", AsyncMock(return_value=True))
    return check


@pytest.fixture
def holder(svc_role: Role) -> _RoleHolder:
    return _RoleHolder(svc_role)


@pytest.fixture
async def client(
    session: AsyncSession, holder: _RoleHolder, entitlement_check: AsyncMock
) -> AsyncGenerator[httpx.AsyncClient, None]:
    app = FastAPI()
    app.include_router(tunnels_router)

    def _role() -> Role:
        return holder.role

    role_dependency = get_args(WorkspaceActorRouteRole)[1].dependency
    app.dependency_overrides[role_dependency] = _role

    async def _session() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_async_session] = _session

    async def _entitlement_required(_: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, EntitlementRequired)
        return JSONResponse(status_code=403, content={"entitlement": exc.entitlement})

    app.add_exception_handler(EntitlementRequired, _entitlement_required)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_create_never_returns_secret(client: httpx.AsyncClient) -> None:
    response = await client.post("/tunnels", json=CREATE_BODY)

    assert response.status_code == 201, response.text
    assert SECRET not in response.text
    body = response.json()
    assert "client_secret" not in body
    assert "encrypted_client_secret" not in body
    assert body["allowed_targets"] == [{"host": "splunk.corp.example", "ports": [8089]}]

    listed = await client.get("/tunnels")
    assert listed.status_code == 200
    assert SECRET not in listed.text
    assert [item["id"] for item in listed.json()["items"]] == [body["id"]]


async def test_duplicate_name_conflicts(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    assert (await client.post("/tunnels", json=CREATE_BODY)).status_code == 201
    response = await client.post("/tunnels", json=CREATE_BODY)
    assert response.status_code == 409
    await session.rollback()


async def test_cross_workspace_requests_return_not_found(
    client: httpx.AsyncClient,
    holder: _RoleHolder,
    svc_role: Role,
    other_workspace: Workspace,
) -> None:
    created = await client.post("/tunnels", json=CREATE_BODY)
    tunnel_id = created.json()["id"]

    holder.role = svc_role.model_copy(update={"workspace_id": other_workspace.id})
    assert (await client.get(f"/tunnels/{tunnel_id}")).status_code == 404
    rotate = await client.patch(
        f"/tunnels/{tunnel_id}", json={"client_secret": "attacker"}
    )
    assert rotate.status_code == 404
    assert (await client.delete(f"/tunnels/{tunnel_id}")).status_code == 404
    listed = await client.get("/tunnels")
    assert listed.json()["items"] == []

    holder.role = svc_role
    fetched = await client.get(f"/tunnels/{tunnel_id}")
    assert fetched.status_code == 200
    assert fetched.json()["workspace_id"] == str(svc_role.workspace_id)


async def test_workspace_id_in_body_is_rejected(
    client: httpx.AsyncClient, other_workspace: Workspace
) -> None:
    response = await client.post(
        "/tunnels", json={**CREATE_BODY, "workspace_id": str(other_workspace.id)}
    )
    assert response.status_code == 422


async def test_routes_require_entitlement(
    client: httpx.AsyncClient, entitlement_check: AsyncMock
) -> None:
    entitlement_check.side_effect = EntitlementRequired(Entitlement.TUNNELS.value)

    response = await client.get("/tunnels")

    assert response.status_code == 403
    assert response.json() == {"entitlement": "tunnels"}
