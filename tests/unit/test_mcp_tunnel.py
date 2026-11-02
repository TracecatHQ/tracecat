import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.db.models import Workspace, WorkspaceTunnel
from tracecat.integrations import mcp_tunnel
from tracecat.integrations.mcp_tunnel import (
    create_mcp_oauth_http_client,
    mcp_oauth_tunnel,
    mcp_oauth_tunnel_proxy,
    mcp_server_target,
    validate_mcp_tunnel,
)
from tracecat.integrations.mcp_validation import MCPConfigurationError

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    ("uri", "target"),
    [
        ("https://splunk.corp.example/mcp", ("splunk.corp.example", 443)),
        ("http://10.0.0.5:8080/mcp", ("10.0.0.5", 8080)),
        ("https://MCP.Corp.Example.:8443", ("mcp.corp.example", 8443)),
    ],
)
def test_mcp_server_target(uri: str, target: tuple[str, int]) -> None:
    assert mcp_server_target(uri) == target


@pytest.mark.usefixtures("db")
async def test_validate_mcp_tunnel(
    session: AsyncSession, svc_role: Role, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mcp_tunnel, "is_org_entitled", AsyncMock(return_value=True))
    assert svc_role.workspace_id is not None
    assert svc_role.organization_id is not None
    tunnel = WorkspaceTunnel(
        workspace_id=svc_role.workspace_id,
        name="onprem",
        endpoint="https://pangolin.example.com",
        client_id="client",
        encrypted_client_secret=b"x",
        allowed_targets=[{"host": "splunk.corp.example", "ports": [443]}],
    )
    other_workspace = Workspace(name="other", organization_id=svc_role.organization_id)
    session.add_all([tunnel, other_workspace])
    await session.commit()

    kwargs = {
        "workspace_id": svc_role.workspace_id,
        "organization_id": svc_role.organization_id,
        "tunnel_id": tunnel.id,
    }
    await validate_mcp_tunnel(
        session, server_uri="https://splunk.corp.example/mcp", **kwargs
    )
    with pytest.raises(ValueError, match="does not allow splunk.corp.example:8089"):
        await validate_mcp_tunnel(
            session, server_uri="https://splunk.corp.example:8089/mcp", **kwargs
        )
    with pytest.raises(ValueError, match="not found"):
        await validate_mcp_tunnel(
            session,
            server_uri="https://splunk.corp.example/mcp",
            workspace_id=other_workspace.id,
            organization_id=svc_role.organization_id,
            tunnel_id=tunnel.id,
        )
    tunnel.enabled = False
    await session.commit()
    with pytest.raises(ValueError, match="disabled"):
        await validate_mcp_tunnel(
            session, server_uri="https://splunk.corp.example/mcp", **kwargs
        )


async def test_validate_mcp_tunnel_requires_entitlement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_tunnel, "is_org_entitled", AsyncMock(return_value=False))
    with pytest.raises(ValueError, match="not available"):
        await validate_mcp_tunnel(
            AsyncMock(),
            workspace_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
            tunnel_id=uuid.uuid4(),
            server_uri="https://splunk.corp.example/mcp",
        )


async def test_oauth_tunnel_scopes_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    resolve = AsyncMock(return_value="http://tunnel:token@10.1.2.3:8080")
    monkeypatch.setattr(mcp_tunnel, "resolve_mcp_tunnel_proxy", resolve)
    ids = {"workspace_id": uuid.uuid4(), "organization_id": uuid.uuid4()}
    async with mcp_oauth_tunnel(tunnel_id=None, subject="x", **ids):
        assert mcp_oauth_tunnel_proxy() is None
    resolve.assert_not_awaited()
    async with mcp_oauth_tunnel(tunnel_id=uuid.uuid4(), subject="x", **ids):
        assert mcp_oauth_tunnel_proxy() == "http://tunnel:token@10.1.2.3:8080"
        async with create_mcp_oauth_http_client() as client:
            assert client._trust_env is False
    assert mcp_oauth_tunnel_proxy() is None


async def test_oauth_tunnel_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from tracecat_ee.tunnels import routing

    monkeypatch.setattr(
        routing,
        "resolve_tunnel_route",
        AsyncMock(side_effect=routing.TunnelRouteError("Tunnel 'x' is disabled")),
    )
    with pytest.raises(MCPConfigurationError, match="disabled"):
        async with mcp_oauth_tunnel(
            workspace_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
            tunnel_id=uuid.uuid4(),
            subject="x",
        ):
            pytest.fail("must not run without the tunnel")
