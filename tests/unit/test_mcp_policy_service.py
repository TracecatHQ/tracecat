"""Unit tests for MCP catalog policy service."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, insert
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.db.models import (
    MCPIntegration,
    MCPIntegrationCatalogEntry,
    Membership,
    Organization,
    OrganizationMembership,
    User,
    Workspace,
)
from tracecat.exceptions import TracecatAuthorizationError, TracecatNotFoundError
from tracecat.identifiers import WorkspaceID
from tracecat.integrations.enums import MCPAuthType, MCPCatalogArtifactType
from tracecat.integrations.mcp_scopes import build_mcp_scope_name
from tracecat.mcp.policy.service import MCPCatalogPolicyService


@pytest.fixture
async def org(session: AsyncSession) -> Organization:
    org = Organization(
        id=uuid.uuid4(),
        name="Policy Org",
        slug=f"policy-org-{uuid.uuid4().hex[:8]}",
    )
    session.add(org)
    await session.commit()
    await session.refresh(org)
    return org


@pytest.fixture
async def user(session: AsyncSession, org: Organization) -> User:
    user = User(
        id=uuid.uuid4(),
        email="policy@example.com",
        hashed_password="test",
    )
    session.add(user)
    await session.flush()
    session.add(
        OrganizationMembership(
            user_id=user.id,
            organization_id=org.id,
        )
    )
    await session.commit()
    await session.refresh(user)
    return user


@pytest.fixture
async def workspace(session: AsyncSession, org: Organization, user: User) -> Workspace:
    workspace = Workspace(
        id=uuid.uuid4(),
        name="Policy Workspace",
        organization_id=org.id,
    )
    session.add(workspace)
    await session.flush()
    session.add(
        Membership(
            user_id=user.id,
            workspace_id=workspace.id,
        )
    )
    await session.commit()
    await session.refresh(workspace)
    return workspace


@pytest.fixture
async def other_org_workspace(session: AsyncSession) -> Workspace:
    org = Organization(
        id=uuid.uuid4(),
        name="Other Org",
        slug=f"other-org-{uuid.uuid4().hex[:8]}",
    )
    workspace = Workspace(
        id=uuid.uuid4(),
        name="Other Workspace",
        organization_id=org.id,
    )
    session.add_all([org, workspace])
    await session.commit()
    await session.refresh(workspace)
    return workspace


@pytest.fixture
def org_admin_role(org: Organization, user: User, workspace: Workspace) -> Role:
    return Role(
        type="user",
        user_id=user.id,
        organization_id=org.id,
        workspace_id=workspace.id,
        service_id="tracecat-mcp",
        scopes=frozenset({"org:workspace:read"}),
    )


@pytest.fixture
def member_role(org: Organization, user: User, workspace: Workspace) -> Role:
    return Role(
        type="user",
        user_id=user.id,
        organization_id=org.id,
        workspace_id=workspace.id,
        service_id="tracecat-mcp",
        scopes=frozenset(),
    )


async def _create_mcp_integration(
    *,
    session: AsyncSession,
    workspace: Workspace,
    scope_namespace: str = "mcppolicy0000001",
) -> MCPIntegration:
    integration = MCPIntegration(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        name="Policy MCP",
        description="Policy MCP",
        slug=f"policy-mcp-{uuid.uuid4().hex[:6]}",
        scope_namespace=scope_namespace,
        server_type="http",
        server_uri="https://api.example.com/mcp",
        auth_type=MCPAuthType.NONE,
        discovery_status="succeeded",
        catalog_version=1,
    )
    session.add(integration)
    await session.commit()
    await session.refresh(integration)
    return integration


async def _insert_catalog_entry(
    *,
    session: AsyncSession,
    integration: MCPIntegration,
    artifact_type: MCPCatalogArtifactType,
    artifact_key: str,
    artifact_ref: str,
    is_active: bool = True,
) -> uuid.UUID:
    entry_id = uuid.uuid4()
    await session.execute(
        insert(MCPIntegrationCatalogEntry).values(
            id=entry_id,
            mcp_integration_id=integration.id,
            workspace_id=integration.workspace_id,
            integration_name=integration.name,
            artifact_type=artifact_type.value,
            artifact_key=artifact_key,
            artifact_ref=artifact_ref,
            display_name=artifact_ref,
            description=f"{artifact_type.value} {artifact_ref}",
            input_schema={"type": "object"},
            artifact_metadata={"origin": "test"},
            raw_payload={"name": artifact_ref},
            content_hash=artifact_key.ljust(64, "0"),
            is_active=is_active,
            search_vector=func.to_tsvector("simple", artifact_ref),
        )
    )
    await session.commit()
    return entry_id


@pytest.mark.anyio
class TestMCPCatalogPolicyService:
    async def test_authorize_catalog_search_allows_org_admin_all_active_entries(
        self,
        session: AsyncSession,
        org_admin_role: Role,
        workspace: Workspace,
    ) -> None:
        integration = await _create_mcp_integration(
            session=session, workspace=workspace
        )
        tool_entry = await _insert_catalog_entry(
            session=session,
            integration=integration,
            artifact_type=MCPCatalogArtifactType.TOOL,
            artifact_key="github-list-repos-a1b2c3d4e5",
            artifact_ref="list_repos",
        )
        resource_entry = await _insert_catalog_entry(
            session=session,
            integration=integration,
            artifact_type=MCPCatalogArtifactType.RESOURCE,
            artifact_key="docs-readme-7f8e9d0c1b",
            artifact_ref="docs://readme",
        )
        await _insert_catalog_entry(
            session=session,
            integration=integration,
            artifact_type=MCPCatalogArtifactType.PROMPT,
            artifact_key="inactive-prompt-6a5b4c3d2e",
            artifact_ref="triage_incident",
            is_active=False,
        )

        service = MCPCatalogPolicyService(session=session, role=org_admin_role)
        result = await service.authorize_catalog_search(workspace_id=workspace.id)

        assert result.is_org_admin is True
        assert result.allowed_entry_ids == frozenset({tool_entry, resource_entry})
        assert len(result.entries) == 2

    async def test_authorize_catalog_search_filters_non_admin_scopes(
        self,
        session: AsyncSession,
        member_role: Role,
        workspace: Workspace,
    ) -> None:
        integration = await _create_mcp_integration(
            session=session, workspace=workspace
        )
        allowed_entry = await _insert_catalog_entry(
            session=session,
            integration=integration,
            artifact_type=MCPCatalogArtifactType.TOOL,
            artifact_key="github-list-repos-a1b2c3d4e5",
            artifact_ref="list_repos",
        )
        await _insert_catalog_entry(
            session=session,
            integration=integration,
            artifact_type=MCPCatalogArtifactType.RESOURCE,
            artifact_key="docs-readme-7f8e9d0c1b",
            artifact_ref="docs://readme",
        )
        allowed_scope, _resource, _action = build_mcp_scope_name(
            scope_namespace=integration.scope_namespace,
            artifact_type=MCPCatalogArtifactType.TOOL,
            artifact_key="github-list-repos-a1b2c3d4e5",
        )
        role = member_role.model_copy(update={"scopes": frozenset({allowed_scope})})

        service = MCPCatalogPolicyService(session=session, role=role)
        result = await service.authorize_catalog_search(workspace_id=workspace.id)

        assert result.is_org_admin is False
        assert result.allowed_scope_names == frozenset({allowed_scope})
        assert result.allowed_entry_ids == frozenset({allowed_entry})
        assert len(result.entries) == 1

    async def test_authorize_catalog_search_accepts_wildcard_scope_grants(
        self,
        session: AsyncSession,
        member_role: Role,
        workspace: Workspace,
    ) -> None:
        integration = await _create_mcp_integration(
            session=session, workspace=workspace
        )
        allowed_entry = await _insert_catalog_entry(
            session=session,
            integration=integration,
            artifact_type=MCPCatalogArtifactType.TOOL,
            artifact_key="github-list-repos-a1b2c3d4e5",
            artifact_ref="list_repos",
        )
        wildcard_scope = f"mcp-tool:{integration.scope_namespace}.*:execute"
        role = member_role.model_copy(update={"scopes": frozenset({wildcard_scope})})

        service = MCPCatalogPolicyService(session=session, role=role)
        result = await service.authorize_catalog_search(workspace_id=workspace.id)

        assert result.is_org_admin is False
        assert result.allowed_entry_ids == frozenset({allowed_entry})
        assert len(result.entries) == 1
        assert result.entries[0].scope_name == (
            f"mcp-tool:{integration.scope_namespace}.github-list-repos-a1b2c3d4e5:execute"
        )

    async def test_get_effective_scopes_uses_rbac_fallback_when_scopes_unset(
        self,
        session: AsyncSession,
        member_role: Role,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        expected_scopes = frozenset({"mcp-tool:mcppolicy0000001.*:execute"})

        async def _compute_effective_scopes(_: Role) -> frozenset[str]:
            return expected_scopes

        monkeypatch.setattr(
            "tracecat.mcp.policy.service.compute_effective_scopes",
            _compute_effective_scopes,
        )
        role = member_role.model_copy(update={"scopes": None})

        service = MCPCatalogPolicyService(session=session, role=role)

        assert await service._get_effective_scopes() == expected_scopes

    async def test_get_effective_scopes_does_not_fallback_for_explicit_empty_scopes(
        self,
        session: AsyncSession,
        member_role: Role,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def _compute_effective_scopes(_: Role) -> frozenset[str]:
            return frozenset({"mcp-tool:mcppolicy0000001.*:execute"})

        monkeypatch.setattr(
            "tracecat.mcp.policy.service.compute_effective_scopes",
            _compute_effective_scopes,
        )
        role = member_role.model_copy(update={"scopes": frozenset()})

        service = MCPCatalogPolicyService(session=session, role=role)

        assert await service._get_effective_scopes() == frozenset()

    async def test_authorize_catalog_entry_rejects_unauthorized_entry(
        self,
        session: AsyncSession,
        member_role: Role,
        workspace: Workspace,
    ) -> None:
        integration = await _create_mcp_integration(
            session=session, workspace=workspace
        )
        entry_id = await _insert_catalog_entry(
            session=session,
            integration=integration,
            artifact_type=MCPCatalogArtifactType.PROMPT,
            artifact_key="triage-incident-6a5b4c3d2e",
            artifact_ref="triage_incident",
        )
        service = MCPCatalogPolicyService(session=session, role=member_role)

        with pytest.raises(TracecatAuthorizationError):
            await service.authorize_catalog_entry(
                workspace_id=workspace.id,
                entry_id=entry_id,
            )

    async def test_authorize_catalog_entries_empty_filter_returns_no_entries(
        self,
        session: AsyncSession,
        org_admin_role: Role,
        workspace: Workspace,
    ) -> None:
        integration = await _create_mcp_integration(
            session=session, workspace=workspace
        )
        await _insert_catalog_entry(
            session=session,
            integration=integration,
            artifact_type=MCPCatalogArtifactType.TOOL,
            artifact_key="github-list-repos-a1b2c3d4e5",
            artifact_ref="list_repos",
        )

        service = MCPCatalogPolicyService(session=session, role=org_admin_role)
        result = await service.authorize_catalog_entries(
            workspace_id=workspace.id,
            entry_ids=[],
        )

        assert result.entries == ()
        assert result.allowed_entry_ids == frozenset()
        assert result.allowed_scope_names == frozenset()

    async def test_authorize_catalog_entry_raises_not_found_for_missing_entry(
        self,
        session: AsyncSession,
        org_admin_role: Role,
        workspace: Workspace,
    ) -> None:
        service = MCPCatalogPolicyService(session=session, role=org_admin_role)

        with pytest.raises(TracecatNotFoundError):
            await service.authorize_catalog_entry(
                workspace_id=workspace.id,
                entry_id=uuid.uuid4(),
            )

    async def test_authorize_catalog_search_rejects_workspace_outside_org(
        self,
        session: AsyncSession,
        org_admin_role: Role,
        other_org_workspace: Workspace,
    ) -> None:
        service = MCPCatalogPolicyService(session=session, role=org_admin_role)

        with pytest.raises(TracecatAuthorizationError):
            await service.authorize_catalog_search(
                workspace_id=WorkspaceID(str(other_org_workspace.id)),
            )
