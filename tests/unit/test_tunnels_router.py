"""API-level tests for workspace tunnels: entitlement gate and IDOR isolation."""

import uuid
from collections.abc import AsyncGenerator
from typing import get_args
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_ee.tunnels import router as tunnels_router_module
from tracecat_ee.tunnels import service as tunnels_service_module
from tracecat_ee.tunnels.router import gateway_router as tunnel_gateway_router
from tracecat_ee.tunnels.router import router as tunnels_router
from tracecat_ee.tunnels.service import TunnelsService
from tracecat_ee.tunnels.tokens import generate_gateway_key, mint_gateway_policy_token

from tracecat import config
from tracecat.auth.dependencies import WorkspaceActorRouteRole
from tracecat.auth.types import Role
from tracecat.db.engine import get_async_session, get_async_session_bypass_rls
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


@pytest.fixture(autouse=True)
def kubernetes_gateways(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "TRACECAT__TUNNEL_GATEWAY_BACKEND", "kubernetes")
    monkeypatch.setattr(tunnels_service_module, "sync_gateway", AsyncMock())
    monkeypatch.setattr(tunnels_service_module, "delete_gateway", AsyncMock())


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


async def test_gateway_key_is_returned_once(
    client: httpx.AsyncClient,
    holder: _RoleHolder,
    svc_role: Role,
    other_workspace: Workspace,
) -> None:
    tunnel_id = (await client.post("/tunnels", json=CREATE_BODY)).json()["id"]
    assert (await client.get(f"/tunnels/{tunnel_id}")).json()["has_gateway_key"] is True

    issued = await client.post(f"/tunnels/{tunnel_id}/gateway-key")
    assert issued.status_code == 201
    key = issued.json()["gateway_key"]
    assert len(key) >= 43

    fetched = await client.get(f"/tunnels/{tunnel_id}")
    assert fetched.json()["has_gateway_key"] is True
    assert key not in fetched.text
    assert key not in (await client.get("/tunnels")).text

    holder.role = svc_role.model_copy(update={"workspace_id": other_workspace.id})
    stolen = await client.post(f"/tunnels/{tunnel_id}/gateway-key")
    assert stolen.status_code == 404


@pytest.fixture
async def gateway_client(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[tuple[httpx.AsyncClient, AsyncMock], None]:
    entitled = AsyncMock(return_value=True)
    monkeypatch.setattr(tunnels_service_module, "is_org_entitled", entitled)
    app = FastAPI()
    app.include_router(tunnel_gateway_router)

    async def _session() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_async_session_bypass_rls] = _session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, entitled


async def test_gateway_policy_follows_tunnel_state(
    client: httpx.AsyncClient,
    gateway_client: tuple[httpx.AsyncClient, AsyncMock],
    svc_role: Role,
) -> None:
    gw, entitled = gateway_client
    tunnel_id = (await client.post("/tunnels", json=CREATE_BODY)).json()["id"]

    async def _policy(key: str) -> httpx.Response:
        token = mint_gateway_policy_token(
            gateway_key=key, tunnel_id=uuid.UUID(tunnel_id)
        )
        return await gw.post(
            "/tunnel-gateway/policy", headers={"Authorization": f"Bearer {token}"}
        )

    # No key issued yet, missing or garbage credentials.
    assert (await _policy("not-the-key")).status_code == 401
    assert (await gw.post("/tunnel-gateway/policy")).status_code == 401
    garbage = {"Authorization": "Bearer not-a-jwt"}
    assert (await gw.post("/tunnel-gateway/policy", headers=garbage)).status_code == 401

    key = (await client.post(f"/tunnels/{tunnel_id}/gateway-key")).json()["gateway_key"]
    response = await _policy(key)
    assert response.status_code == 200
    assert response.json() == {
        "tunnel_id": tunnel_id,
        "workspace_id": str(svc_role.workspace_id),
        "allowed_targets": CREATE_BODY["allowed_targets"],
        "managed": False,
    }
    assert SECRET not in response.text
    assert key not in response.text

    # Another tunnel's key can't read this tunnel's policy.
    assert (await _policy(generate_gateway_key())).status_code == 401

    await client.patch(f"/tunnels/{tunnel_id}", json={"enabled": False})
    assert (await _policy(key)).status_code == 403
    await client.patch(f"/tunnels/{tunnel_id}", json={"enabled": True})
    assert (await _policy(key)).status_code == 200

    entitled.return_value = False
    assert (await _policy(key)).status_code == 403
    entitled.return_value = True

    new_key = (await client.post(f"/tunnels/{tunnel_id}/gateway-key")).json()[
        "gateway_key"
    ]
    assert (await _policy(key)).status_code == 401
    assert (await _policy(new_key)).status_code == 200

    assert (await client.delete(f"/tunnels/{tunnel_id}")).status_code == 204
    assert (await _policy(new_key)).status_code == 401


MANAGED_BODY = {
    "name": "managed-splunk",
    "allowed_targets": [{"host": "splunk.corp.example", "ports": [8089]}],
}


@pytest.fixture
def fake_pangolin(monkeypatch: pytest.MonkeyPatch) -> type:
    from tracecat_ee.tunnels import service as service_module
    from tracecat_ee.tunnels.pangolin import (
        PangolinClient,
        PangolinCredentials,
        PangolinError,
        PangolinSettings,
        PangolinSite,
    )

    settings = PangolinSettings(
        api_url="https://pangolin-api.example.com/v1",
        endpoint="https://pangolin.example.com",
        org_id="tracecat",
        api_key="org-api-key",
    )

    class FakeAPI:
        fail = False

        def __init__(self, _: PangolinSettings) -> None:
            pass

        async def __aenter__(self) -> "FakeAPI":
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def create_site(self, name: str) -> PangolinSite:
            if FakeAPI.fail:
                raise PangolinError("boom")
            return PangolinSite(1, PangolinCredentials("newt-id", "newt-secret"))

        async def create_machine_client(self, name: str) -> PangolinClient:
            return PangolinClient(2, PangolinCredentials("olm-id", "olm-secret"))

        async def create_host_resource(self, **_: object) -> int:
            return 3

        async def site_online(self, site_id: int) -> bool:
            return True

        async def delete_resource(self, _: int) -> None:
            return None

        async def delete_client(self, _: int) -> None:
            return None

        async def delete_site(self, _: int) -> None:
            return None

    monkeypatch.setattr(service_module, "PangolinAPI", FakeAPI)
    monkeypatch.setattr(
        service_module,
        "resolve_pangolin_settings",
        AsyncMock(return_value=settings),
    )
    return FakeAPI


async def test_managed_create_returns_site_credentials_once(
    client: httpx.AsyncClient, fake_pangolin: type
) -> None:
    response = await client.post("/tunnels/managed", json=MANAGED_BODY)

    assert response.status_code == 201
    body = response.json()
    assert body["tunnel"]["managed"] is True
    assert body["site_secret"] == "newt-secret"
    assert "olm-secret" not in response.text

    tunnel_id = body["tunnel"]["id"]
    read = await client.get(f"/tunnels/{tunnel_id}")
    assert "newt-secret" not in read.text and "olm-secret" not in read.text
    status_response = await client.get(f"/tunnels/{tunnel_id}/status")
    assert status_response.json()["site_online"] is True

    duplicate = await client.post("/tunnels/managed", json=MANAGED_BODY)
    assert duplicate.status_code == 409


async def test_managed_create_pangolin_failure_is_502(
    client: httpx.AsyncClient, fake_pangolin: type
) -> None:
    fake_pangolin.fail = True  # pyright: ignore[reportAttributeAccessIssue]

    response = await client.post("/tunnels/managed", json=MANAGED_BODY)

    assert response.status_code == 502
    assert "boom" not in response.text


async def test_managed_status_cross_workspace_is_404(
    client: httpx.AsyncClient,
    holder: _RoleHolder,
    other_workspace: Workspace,
    fake_pangolin: type,
) -> None:
    created = await client.post("/tunnels/managed", json=MANAGED_BODY)
    tunnel_id = created.json()["tunnel"]["id"]

    holder.role = holder.role.model_copy(update={"workspace_id": other_workspace.id})
    response = await client.get(f"/tunnels/{tunnel_id}/status")

    assert response.status_code == 404


async def test_tunnels_without_gateway_backend_are_unavailable(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert (await client.get("/tunnels/availability")).json() == {"available": True}
    tunnel_id = (await client.post("/tunnels", json=CREATE_BODY)).json()["id"]
    monkeypatch.setattr(config, "TRACECAT__TUNNEL_GATEWAY_BACKEND", "none")

    assert (await client.get("/tunnels/availability")).json() == {"available": False}
    created = await client.post("/tunnels", json={**CREATE_BODY, "name": "another"})
    assert created.status_code == 422
    assert "Kubernetes" in created.json()["detail"]
    issued = await client.post(f"/tunnels/{tunnel_id}/gateway-key")
    assert issued.status_code == 422
