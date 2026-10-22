from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_ee.tunnels import service as service_module
from tracecat_ee.tunnels.gateways import TunnelGatewayUnavailableError
from tracecat_ee.tunnels.routing import TunnelRouteError, resolve_tunnel_route
from tracecat_ee.tunnels.schemas import TunnelCreate, TunnelUpdate
from tracecat_ee.tunnels.service import TunnelsService

from tracecat import config
from tracecat.auth.secrets import get_db_encryption_key
from tracecat.auth.types import Role
from tracecat.authz.scopes import VIEWER_SCOPES
from tracecat.db.models import Organization, Workspace, WorkspaceTunnel
from tracecat.exceptions import (
    EntitlementRequired,
    ScopeDeniedError,
    TracecatNotFoundError,
)
from tracecat.pagination import PageParams
from tracecat.secrets.encryption import decrypt_value

pytestmark = [pytest.mark.usefixtures("db"), pytest.mark.anyio]

SECRET = "machine-client-secret"


@pytest.fixture(autouse=True)
def kubernetes_gateways(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "TRACECAT__TUNNEL_GATEWAY_BACKEND", "kubernetes")
    monkeypatch.setattr(service_module, "sync_gateway", AsyncMock())
    monkeypatch.setattr(service_module, "delete_gateway", AsyncMock())


@pytest.fixture(autouse=True)
def entitled(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock = AsyncMock(return_value=True)
    monkeypatch.setattr(TunnelsService, "has_entitlement", mock)
    return mock


@pytest.fixture
async def other_workspace(
    session: AsyncSession, svc_organization: Organization
) -> AsyncGenerator[Workspace, None]:
    workspace = Workspace(name="other-workspace", organization_id=svc_organization.id)
    session.add(workspace)
    await session.commit()
    yield workspace


@pytest.fixture
def service(session: AsyncSession, svc_role: Role) -> TunnelsService:
    return TunnelsService(session=session, role=svc_role)


@pytest.fixture
def other_service(
    session: AsyncSession, svc_role: Role, other_workspace: Workspace
) -> TunnelsService:
    role = svc_role.model_copy(update={"workspace_id": other_workspace.id})
    return TunnelsService(session=session, role=role)


def _params(name: str = "onprem-splunk") -> TunnelCreate:
    return TunnelCreate.model_validate(
        {
            "name": name,
            "endpoint": "https://pangolin.example.com",
            "client_id": "client-123",
            "client_secret": SECRET,
            "allowed_targets": [{"host": "splunk.corp.example", "ports": [8089]}],
        }
    )


async def test_create_encrypts_client_secret(service: TunnelsService) -> None:
    tunnel = await service.create_tunnel(_params())

    assert tunnel.workspace_id == service.workspace_id
    assert tunnel.provider == "pangolin"
    assert tunnel.allowed_targets == [{"host": "splunk.corp.example", "ports": [8089]}]
    assert SECRET.encode() not in tunnel.encrypted_client_secret
    decrypted = decrypt_value(
        tunnel.encrypted_client_secret, key=get_db_encryption_key()
    )
    assert decrypted.decode() == SECRET


async def test_get_and_list_are_scoped_to_workspace(
    service: TunnelsService, other_service: TunnelsService
) -> None:
    own = await service.create_tunnel(_params())
    other = await other_service.create_tunnel(_params())

    assert (await service.get_tunnel(own.id)).id == own.id
    assert (await service.get_tunnel_by_name("onprem-splunk")).id == own.id
    page = await service.list_tunnels(PageParams(limit=10))
    assert [tunnel.id for tunnel in page.items] == [own.id]
    with pytest.raises(TracecatNotFoundError):
        await service.get_tunnel(other.id)


async def test_cross_workspace_access_is_not_found(
    session: AsyncSession, service: TunnelsService, other_service: TunnelsService
) -> None:
    victim = await service.create_tunnel(_params())

    with pytest.raises(TracecatNotFoundError):
        await other_service.get_tunnel(victim.id)
    with pytest.raises(TracecatNotFoundError):
        await other_service.get_tunnel_by_name(victim.name)
    with pytest.raises(TracecatNotFoundError):
        await other_service.update_tunnel(
            victim, TunnelUpdate.model_validate({"client_secret": "attacker"})
        )
    with pytest.raises(TracecatNotFoundError):
        await other_service.delete_tunnel(victim)
    assert (await other_service.list_tunnels(PageParams(limit=10))).items == []

    await session.refresh(victim)
    decrypted = decrypt_value(
        victim.encrypted_client_secret, key=get_db_encryption_key()
    )
    assert decrypted.decode() == SECRET
    assert (await service.get_tunnel(victim.id)).id == victim.id


async def test_names_are_unique_per_workspace(
    session: AsyncSession, service: TunnelsService, other_service: TunnelsService
) -> None:
    await service.create_tunnel(_params())
    await other_service.create_tunnel(_params())

    with pytest.raises(IntegrityError):
        await service.create_tunnel(_params())
    await session.rollback()


async def test_update_rotates_secret_and_targets(service: TunnelsService) -> None:
    tunnel = await service.create_tunnel(_params())
    updated = await service.update_tunnel(
        tunnel,
        TunnelUpdate.model_validate(
            {
                "client_secret": "rotated",
                "allowed_targets": [{"host": "10.0.0.5", "ports": [443, 8089]}],
                "enabled": False,
            }
        ),
    )

    decrypted = decrypt_value(
        updated.encrypted_client_secret, key=get_db_encryption_key()
    )
    assert decrypted.decode() == "rotated"
    assert updated.allowed_targets == [{"host": "10.0.0.5", "ports": [443, 8089]}]
    assert updated.enabled is False
    assert updated.client_id == "client-123"


async def test_delete_removes_tunnel(service: TunnelsService) -> None:
    tunnel = await service.create_tunnel(_params())
    await service.delete_tunnel(tunnel)
    with pytest.raises(TracecatNotFoundError):
        await service.get_tunnel(tunnel.id)


async def test_requires_entitlement(
    session: AsyncSession, service: TunnelsService, entitled: AsyncMock
) -> None:
    tunnel = await service.create_tunnel(_params())
    entitled.return_value = False

    with pytest.raises(EntitlementRequired):
        await service.create_tunnel(_params("another"))
    with pytest.raises(EntitlementRequired):
        await service.get_tunnel(tunnel.id)
    with pytest.raises(EntitlementRequired):
        await service.get_tunnel_by_name(tunnel.name)
    with pytest.raises(EntitlementRequired):
        await service.list_tunnels(PageParams(limit=10))
    with pytest.raises(EntitlementRequired):
        await service.update_tunnel(tunnel, TunnelUpdate(enabled=False))
    with pytest.raises(EntitlementRequired):
        await service.delete_tunnel(tunnel)
    assert await session.get(WorkspaceTunnel, tunnel.surrogate_id) is not None


async def test_viewer_cannot_manage_tunnels(
    session: AsyncSession, service: TunnelsService, svc_role: Role
) -> None:
    tunnel = await service.create_tunnel(_params())
    viewer = TunnelsService(
        session=session, role=svc_role.model_copy(update={"scopes": VIEWER_SCOPES})
    )

    assert (await viewer.get_tunnel(tunnel.id)).id == tunnel.id
    with pytest.raises(ScopeDeniedError):
        await viewer.create_tunnel(_params("another"))
    with pytest.raises(ScopeDeniedError):
        await viewer.update_tunnel(tunnel, TunnelUpdate(enabled=False))
    with pytest.raises(ScopeDeniedError):
        await viewer.delete_tunnel(tunnel)


async def test_gateway_key_issue_and_rotate(service: TunnelsService) -> None:
    tunnel = await service.create_tunnel(_params())
    assert tunnel.encrypted_gateway_key is not None

    first = await service.issue_gateway_key(tunnel)
    assert tunnel.encrypted_gateway_key is not None
    assert first.encode() not in tunnel.encrypted_gateway_key
    assert await service.get_gateway_key(tunnel) == first

    second = await service.issue_gateway_key(tunnel)
    assert second != first
    assert await service.get_gateway_key(tunnel) == second


async def test_gateway_key_cross_workspace_is_not_found(
    service: TunnelsService, other_service: TunnelsService
) -> None:
    victim = await service.create_tunnel(_params())
    key = await service.issue_gateway_key(victim)

    with pytest.raises(TracecatNotFoundError):
        await other_service.issue_gateway_key(victim)
    with pytest.raises(TracecatNotFoundError):
        await other_service.get_gateway_key(victim)
    assert await service.get_gateway_key(victim) == key


async def test_viewer_cannot_issue_gateway_key(
    session: AsyncSession, service: TunnelsService, svc_role: Role
) -> None:
    tunnel = await service.create_tunnel(_params())
    viewer = TunnelsService(
        session=session, role=svc_role.model_copy(update={"scopes": VIEWER_SCOPES})
    )
    with pytest.raises(ScopeDeniedError):
        await viewer.issue_gateway_key(tunnel)


async def test_delete_in_use_lists_every_reference(
    service: TunnelsService, session: AsyncSession, svc_role: Role
) -> None:
    from tracecat_ee.tunnels.schemas import TunnelReferenceKind
    from tracecat_ee.tunnels.service import TunnelInUseError

    from tracecat.db.models import Action, AgentPreset, MCPIntegration, Workflow
    from tracecat.integrations.enums import MCPAuthType

    tunnel = await service.create_tunnel(_params())
    other = await service.create_tunnel(_params("unused"))
    assert svc_role.workspace_id is not None
    workspace_id = svc_role.workspace_id
    mcp = MCPIntegration(
        workspace_id=workspace_id,
        name="Private Splunk",
        slug="private-splunk",
        server_type="http",
        server_uri="https://splunk.corp.example:8089/mcp",
        auth_type=MCPAuthType.NONE,
        tunnel_id=tunnel.id,
    )
    preset = AgentPreset(
        workspace_id=workspace_id,
        name="Triage",
        slug="triage",
        model_name="claude",
        model_provider="anthropic",
        tool_tunnels={"tools.sublime.list_messages": tunnel.name},
    )
    unrelated_preset = AgentPreset(
        workspace_id=workspace_id,
        name="Unrelated",
        slug="unrelated",
        model_name="claude",
        model_provider="anthropic",
        tool_tunnels={"tools.sublime.list_messages": other.name},
    )
    workflow = Workflow(
        workspace_id=workspace_id,
        title="Enrich",
        description="",
        config={"tunnel": tunnel.name},
    )
    session.add_all([mcp, preset, unrelated_preset, workflow])
    await session.flush()
    session.add(
        Action(
            workspace_id=workspace_id,
            workflow_id=workflow.id,
            type="core.http_request",
            title="Query Splunk",
            description="",
            control_flow={"tunnel": tunnel.name},
        )
    )
    await session.commit()

    references = await service.list_tunnel_references(tunnel)
    by_kind = {ref.kind: ref for ref in references}
    assert set(by_kind) == {
        TunnelReferenceKind.MCP_INTEGRATION,
        TunnelReferenceKind.AGENT_PRESET,
        TunnelReferenceKind.WORKFLOW,
    }
    assert by_kind[TunnelReferenceKind.MCP_INTEGRATION].id == mcp.id
    assert by_kind[TunnelReferenceKind.AGENT_PRESET].id == preset.id
    assert (
        by_kind[TunnelReferenceKind.AGENT_PRESET].detail
        == "tools.sublime.list_messages"
    )
    assert (
        by_kind[TunnelReferenceKind.WORKFLOW].detail == "Workflow default; Query Splunk"
    )

    with pytest.raises(TunnelInUseError) as exc_info:
        await service.delete_tunnel(tunnel)
    assert len(exc_info.value.referenced_by) == 3
    assert await service.get_tunnel(tunnel.id)

    other_references = await service.list_tunnel_references(other)
    assert [ref.id for ref in other_references] == [unrelated_preset.id]

    with pytest.raises(TunnelInUseError) as rename_exc:
        await service.update_tunnel(other, TunnelUpdate(name="renamed"))
    assert [ref.id for ref in rename_exc.value.referenced_by] == [unrelated_preset.id]
    await service.update_tunnel(other, TunnelUpdate(name=other.name))

    await session.delete(unrelated_preset)
    await session.commit()
    renamed = await service.update_tunnel(other, TunnelUpdate(name="renamed"))
    assert renamed.name == "renamed"


async def test_tunnels_require_kubernetes_gateway_backend(
    service: TunnelsService, monkeypatch: pytest.MonkeyPatch
) -> None:
    tunnel = await service.create_tunnel(_params())
    monkeypatch.setattr(config, "TRACECAT__TUNNEL_GATEWAY_BACKEND", "none")

    with pytest.raises(TunnelGatewayUnavailableError, match="Kubernetes"):
        await service.create_tunnel(_params("another"))
    with pytest.raises(TunnelGatewayUnavailableError, match="Kubernetes"):
        await service.issue_gateway_key(tunnel)
    with pytest.raises(TunnelRouteError, match="Kubernetes"):
        await resolve_tunnel_route(
            workspace_id=tunnel.workspace_id,
            organization_id=service.organization_id,
            subject="test",
            tunnel_id=tunnel.id,
        )


async def test_route_to_missing_named_tunnel_has_actionable_error(
    service: TunnelsService, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale name, e.g. in a pinned preset version, fails with a clear error."""
    from contextlib import asynccontextmanager

    from tracecat_ee.tunnels import routing

    @asynccontextmanager
    async def _session():
        yield session

    monkeypatch.setattr(
        routing, "get_async_session_bypass_rls_context_manager", _session
    )
    monkeypatch.setattr(routing, "is_org_entitled", AsyncMock(return_value=True))

    with pytest.raises(
        TunnelRouteError,
        match=r"Tunnel 'deleted-tunnel' not found in this workspace\. Create it",
    ):
        await resolve_tunnel_route(
            workspace_id=service.workspace_id,
            organization_id=service.organization_id,
            subject="test",
            name="deleted-tunnel",
        )


async def test_published_workflow_definition_references_tunnel(
    service: TunnelsService, session: AsyncSession, svc_role: Role
) -> None:
    """A draft that dropped the tunnel still counts while the published run uses it."""
    from tracecat_ee.tunnels.schemas import TunnelReferenceKind

    from tracecat.db.models import Workflow, WorkflowDefinition

    tunnel = await service.create_tunnel(_params())
    assert svc_role.workspace_id is not None
    workflow = Workflow(
        workspace_id=svc_role.workspace_id,
        title="Published",
        description="",
        version=2,
    )
    session.add(workflow)
    await session.flush()
    session.add_all(
        [
            WorkflowDefinition(
                workspace_id=svc_role.workspace_id,
                workflow_id=workflow.id,
                version=1,
                content={"actions": [{"ref": "old", "tunnel": "elsewhere"}]},
            ),
            WorkflowDefinition(
                workspace_id=svc_role.workspace_id,
                workflow_id=workflow.id,
                version=2,
                content={"actions": [{"ref": "query", "tunnel": tunnel.name}]},
            ),
        ]
    )
    await session.commit()

    [reference] = await service.list_tunnel_references(tunnel)
    assert reference.kind is TunnelReferenceKind.WORKFLOW
    assert reference.id == workflow.id
    assert reference.detail == "Published version"
