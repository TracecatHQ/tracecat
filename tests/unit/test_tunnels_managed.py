import json
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_ee.tunnels import service as service_module
from tracecat_ee.tunnels.pangolin import (
    PangolinAPI,
    PangolinClient,
    PangolinCredentials,
    PangolinError,
    PangolinSettings,
    PangolinSite,
)
from tracecat_ee.tunnels.schemas import ManagedTunnelCreate, TunnelUpdate
from tracecat_ee.tunnels.service import TunnelsService

from tracecat import config
from tracecat.auth.secrets import get_db_encryption_key
from tracecat.auth.types import Role
from tracecat.db.models import Organization, Workspace, WorkspaceTunnel
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.secrets.encryption import decrypt_value

SETTINGS = PangolinSettings(
    api_url="https://pangolin-api.example.com/v1",
    endpoint="https://pangolin.example.com",
    org_id="tracecat",
    api_key="org-api-key",
)


class FakePangolinAPI:
    """In-memory stand-in for the Pangolin Integration API."""

    instances: list["FakePangolinAPI"] = []
    fail_on_resource: str | None = None
    online = True
    grants: dict[int, set[int]] = {}

    def __init__(self, settings: PangolinSettings) -> None:
        self.settings = settings
        self.calls: list[tuple[str, Any]] = []
        FakePangolinAPI.instances.append(self)

    async def __aenter__(self) -> "FakePangolinAPI":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def create_site(self, name: str) -> PangolinSite:
        self.calls.append(("create_site", name))
        return PangolinSite(7, PangolinCredentials("newt-id", "newt-secret"))

    async def site_online(self, site_id: int) -> bool:
        return self.online

    async def create_machine_client(self, name: str) -> PangolinClient:
        self.calls.append(("create_client", name))
        return PangolinClient(11, PangolinCredentials("olm-id", "olm-secret"))

    async def create_host_resource(
        self,
        *,
        name: str,
        site_id: int,
        destination: str,
        tcp_ports: list[int],
        client_id: int,
        alias: str | None = None,
    ) -> int:
        self.calls.append(("alias", (destination, alias)))
        if destination == self.fail_on_resource:
            raise PangolinError("resource failed")
        resource_id = 100 + len(self.grants)
        self.grants[resource_id] = {client_id}
        self.calls.append(("create_resource", (destination, tcp_ports, client_id)))
        return resource_id

    async def delete_resource(self, resource_id: int) -> None:
        self.calls.append(("delete_resource", resource_id))

    async def delete_client(self, client_id: int) -> None:
        self.calls.append(("delete_client", client_id))

    async def delete_site(self, site_id: int) -> None:
        self.calls.append(("delete_site", site_id))


def _calls() -> list[tuple[str, Any]]:
    return [c for api in FakePangolinAPI.instances for c in api.calls]


def _params(name: str = "managed-splunk") -> ManagedTunnelCreate:
    return ManagedTunnelCreate.model_validate(
        {
            "name": name,
            "allowed_targets": [
                {"host": "splunk.corp.example", "ports": [8089, 8443]},
                {"host": "10.0.0.5", "ports": [443]},
            ],
        }
    )


@pytest.mark.anyio
class TestPangolinAPI:
    async def test_provisioning_requests(self) -> None:
        requests: list[httpx.Request] = []
        responses = {
            ("GET", "/v1/org/tracecat/pick-site-defaults"): {
                "newtId": "n1",
                "newtSecret": "s1",
                "subnet": "100.89.128.8/30",
                "exitNodeId": 1,
                "clientAddress": "100.90.128.2",
            },
            ("PUT", "/v1/org/tracecat/site"): {"siteId": 2},
            ("GET", "/v1/org/tracecat/pick-client-defaults"): {
                "olmId": "o1",
                "olmSecret": "s2",
                "subnet": "100.90.128.3",
            },
            ("PUT", "/v1/org/tracecat/client"): {"clientId": 3},
            ("PUT", "/v1/org/tracecat/private-resource"): {"siteResourceId": 4},
            ("GET", "/v1/private-resource/4/clients"): {"clients": [{"clientId": 3}]},
        }

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            assert request.headers["Authorization"] == "Bearer org-api-key"
            data = responses[(request.method, request.url.path)]
            return httpx.Response(201, json={"data": data, "success": True})

        async with PangolinAPI(SETTINGS, transport=httpx.MockTransport(handler)) as api:
            site = await api.create_site("site")
            client = await api.create_machine_client("client")
            resource_id = await api.create_host_resource(
                name="r",
                site_id=site.site_id,
                destination="splunk.corp.example",
                tcp_ports=[8443, 8089],
                client_id=client.client_id,
                alias="splunk-corp-example.0123456789abcdef.tunnel.internal",
            )

        assert site == PangolinSite(2, PangolinCredentials("n1", "s1"))
        assert client == PangolinClient(3, PangolinCredentials("o1", "s2"))
        assert resource_id == 4
        resource_body = json.loads(requests[4].content)
        assert resource_body == {
            "name": "r",
            "mode": "host",
            "siteId": 2,
            "destination": "splunk.corp.example",
            "tcpPortRangeString": "8089,8443",
            "udpPortRangeString": "",
            "disableIcmp": True,
            "userIds": [],
            "roleIds": [],
            "clientIds": [3],
            "alias": "splunk-corp-example.0123456789abcdef.tunnel.internal",
        }
        assert json.loads(requests[1].content)["secret"] == "s1"
        assert json.loads(requests[3].content)["type"] == "olm"

    async def test_error_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={"success": False, "message": "denied"})

        async with PangolinAPI(SETTINGS, transport=httpx.MockTransport(handler)) as api:
            with pytest.raises(PangolinError, match="denied"):
                await api.delete_site(1)

    async def test_delete_treats_missing_as_deleted(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"success": False, "message": "gone"})

        async with PangolinAPI(SETTINGS, transport=httpx.MockTransport(handler)) as api:
            await api.delete_resource(1)
            await api.delete_client(2)
            await api.delete_site(3)
            with pytest.raises(PangolinError):
                await api.site_online(3)

    def test_settings_require_all_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tracecat import config

        monkeypatch.setattr(config, "TRACECAT__PANGOLIN_API_URL", SETTINGS.api_url)
        monkeypatch.setattr(config, "TRACECAT__PANGOLIN_ENDPOINT", SETTINGS.endpoint)
        monkeypatch.setattr(config, "TRACECAT__PANGOLIN_ORG_ID", SETTINGS.org_id)
        monkeypatch.setattr(config, "TRACECAT__PANGOLIN_API_KEY", None)
        assert PangolinSettings.from_config() is None
        monkeypatch.setattr(config, "TRACECAT__PANGOLIN_API_KEY", SETTINGS.api_key)
        assert PangolinSettings.from_config() == SETTINGS


@pytest.mark.usefixtures("db")
@pytest.mark.anyio
class TestManagedTunnels:
    @pytest.fixture(autouse=True)
    def fake_pangolin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(config, "TRACECAT__TUNNEL_GATEWAY_BACKEND", "kubernetes")
        monkeypatch.setattr(service_module, "sync_gateway", AsyncMock())
        monkeypatch.setattr(
            TunnelsService, "has_entitlement", AsyncMock(return_value=True)
        )
        monkeypatch.setattr(service_module, "PangolinAPI", FakePangolinAPI)
        monkeypatch.setattr(
            service_module,
            "resolve_pangolin_settings",
            AsyncMock(return_value=SETTINGS),
        )
        FakePangolinAPI.instances = []
        FakePangolinAPI.fail_on_resource = None
        FakePangolinAPI.online = True
        FakePangolinAPI.grants = {}

    @pytest.fixture
    def service(self, session: AsyncSession, svc_role: Role) -> TunnelsService:
        return TunnelsService(session=session, role=svc_role)

    @pytest.fixture
    async def other_service(
        self, session: AsyncSession, svc_role: Role, svc_organization: Organization
    ) -> AsyncGenerator[TunnelsService, None]:
        workspace = Workspace(
            name="other-workspace", organization_id=svc_organization.id
        )
        session.add(workspace)
        await session.commit()
        role = svc_role.model_copy(update={"workspace_id": workspace.id})
        yield TunnelsService(session=session, role=role)

    async def test_create_provisions_and_stores_machine_client(
        self, service: TunnelsService
    ) -> None:
        created = await service.create_managed_tunnel(_params())

        tunnel = await service.get_tunnel(created.tunnel.id)
        assert tunnel.managed is True
        assert tunnel.endpoint == SETTINGS.endpoint
        assert tunnel.client_id == "olm-id"
        assert (
            decrypt_value(tunnel.encrypted_client_secret, key=get_db_encryption_key())
            == b"olm-secret"
        )
        assert tunnel.pangolin_site_id == 7
        assert tunnel.pangolin_client_id == 11
        assert tunnel.pangolin_resource_ids == {
            "splunk.corp.example": 100,
            "10.0.0.5": 101,
        }
        assert (
            "create_resource",
            ("splunk.corp.example", [8089, 8443], 11),
        ) in _calls()
        assert ("create_resource", ("10.0.0.5", [443], 11)) in _calls()
        hex_id = created.tunnel.id.hex[:16]
        assert ("alias", ("10.0.0.5", None)) in _calls()
        assert (
            "alias",
            ("splunk.corp.example", f"splunk-corp-example.{hex_id}.tunnel.internal"),
        ) in _calls()
        assert created.site_secret == "newt-secret"
        assert created.site_install_command == (
            "newt --id newt-id --secret newt-secret --endpoint https://pangolin.example.com"
        )
        assert "olm-secret" not in created.model_dump_json()

    async def test_failure_rolls_back_pangolin_and_db(
        self, service: TunnelsService, session: AsyncSession
    ) -> None:
        FakePangolinAPI.fail_on_resource = "10.0.0.5"

        with pytest.raises(PangolinError):
            await service.create_managed_tunnel(_params())

        assert ("delete_resource", 100) in _calls()
        assert ("delete_client", 11) in _calls()
        assert ("delete_site", 7) in _calls()
        rows = await session.execute(
            select(WorkspaceTunnel).where(WorkspaceTunnel.name == "managed-splunk")
        )
        assert rows.scalars().first() is None

    async def test_not_configured(
        self, service: TunnelsService, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            service_module, "resolve_pangolin_settings", AsyncMock(return_value=None)
        )
        with pytest.raises(TracecatValidationError, match="not configured"):
            await service.create_managed_tunnel(_params())
        assert _calls() == []

    async def test_delete_removes_pangolin_objects(
        self, service: TunnelsService
    ) -> None:
        created = await service.create_managed_tunnel(_params())
        tunnel = await service.get_tunnel(created.tunnel.id)

        await service.delete_tunnel(tunnel)

        assert ("delete_resource", 100) in _calls()
        assert ("delete_resource", 101) in _calls()
        assert ("delete_client", 11) in _calls()
        assert ("delete_site", 7) in _calls()
        with pytest.raises(TracecatNotFoundError):
            await service.get_tunnel(created.tunnel.id)

    async def test_managed_fields_are_immutable(self, service: TunnelsService) -> None:
        created = await service.create_managed_tunnel(_params())
        tunnel = await service.get_tunnel(created.tunnel.id)

        update = TunnelUpdate.model_validate(
            {"allowed_targets": [{"host": "other.corp.example", "ports": [22]}]}
        )
        with pytest.raises(TracecatValidationError, match="managed tunnel"):
            await service.update_tunnel(tunnel, update)

        renamed = await service.update_tunnel(
            tunnel, TunnelUpdate.model_validate({"description": "Splunk"})
        )
        assert renamed.description == "Splunk"

    async def test_status(self, service: TunnelsService) -> None:
        created = await service.create_managed_tunnel(_params())
        tunnel = await service.get_tunnel(created.tunnel.id)

        status = await service.get_tunnel_status(tunnel)
        assert status.site_online is True
        # Gateways are not run by Tracecat with the default backend.
        assert status.gateway_ready is None

    async def test_managed_tunnel_gets_gateway_key(
        self, service: TunnelsService
    ) -> None:
        created = await service.create_managed_tunnel(_params())
        tunnel = await service.get_tunnel(created.tunnel.id)

        assert tunnel.encrypted_gateway_key is not None
        assert await service.get_gateway_key(tunnel)

    async def test_cross_workspace_status_is_not_found(
        self, service: TunnelsService, other_service: TunnelsService
    ) -> None:
        created = await service.create_managed_tunnel(_params())

        with pytest.raises(TracecatNotFoundError):
            await other_service.get_tunnel(created.tunnel.id)
        tunnel = await service.get_tunnel(created.tunnel.id)
        with pytest.raises(TracecatNotFoundError):
            await other_service.get_tunnel_status(tunnel)
        with pytest.raises(TracecatNotFoundError):
            await other_service.delete_tunnel(tunnel)
        assert not any(c[0].startswith("delete") for c in _calls())
