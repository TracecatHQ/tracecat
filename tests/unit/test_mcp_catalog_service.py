"""Unit tests for persisted MCP catalog search service."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, insert, text
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
from tracecat.integrations.enums import MCPAuthType, MCPCatalogArtifactType
from tracecat.integrations.mcp_scopes import build_mcp_scope_name
from tracecat.mcp.catalog.service import MCPCatalogSearchService, _escape_like_pattern


@pytest.fixture
async def org(session: AsyncSession) -> Organization:
    org = Organization(
        id=uuid.uuid4(),
        name="Catalog Org",
        slug=f"catalog-org-{uuid.uuid4().hex[:8]}",
    )
    session.add(org)
    await session.commit()
    await session.refresh(org)
    return org


@pytest.fixture(autouse=True)
async def enable_pg_trgm(session: AsyncSession) -> None:
    await session.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    await session.commit()


@pytest.fixture
async def user(session: AsyncSession, org: Organization) -> User:
    user = User(
        id=uuid.uuid4(),
        email="catalog@example.com",
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
        name="Catalog Workspace",
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
    scope_namespace: str | None = None,
) -> MCPIntegration:
    integration = MCPIntegration(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        name="Catalog MCP",
        description="Catalog MCP",
        slug=f"catalog-mcp-{uuid.uuid4().hex[:6]}",
        scope_namespace=scope_namespace or uuid.uuid4().hex[:16],
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
    display_name: str | None = None,
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
            display_name=display_name,
            description=f"{artifact_type.value} {artifact_ref}",
            input_schema={"type": "object"},
            artifact_metadata={"origin": "test"},
            raw_payload={"name": artifact_ref},
            content_hash=artifact_key.ljust(64, "0"),
            is_active=is_active,
            search_vector=func.to_tsvector(
                "simple", f"{display_name or ''} {artifact_ref}".strip()
            ),
        )
    )
    await session.commit()
    return entry_id


@pytest.mark.anyio
class TestMCPCatalogSearchService:
    async def test_escape_like_pattern_treats_wildcards_literally(self) -> None:
        assert _escape_like_pattern(r"tools.%_catalog\name") == (
            r"tools.\%\_catalog\\name"
        )

    async def test_search_catalog_ranks_exact_matches_first(
        self,
        session: AsyncSession,
        org_admin_role: Role,
        workspace: Workspace,
    ) -> None:
        integration = await _create_mcp_integration(
            session=session, workspace=workspace
        )
        exact_entry = await _insert_catalog_entry(
            session=session,
            integration=integration,
            artifact_type=MCPCatalogArtifactType.TOOL,
            artifact_key="github-list-repos-a1b2c3d4e5",
            artifact_ref="list_repos",
            display_name="List repos",
        )
        fuzzy_entry = await _insert_catalog_entry(
            session=session,
            integration=integration,
            artifact_type=MCPCatalogArtifactType.TOOL,
            artifact_key="github-list-org-repos-a1b2c3d4e6",
            artifact_ref="list_org_repos",
            display_name="List organization repos",
        )
        await _insert_catalog_entry(
            session=session,
            integration=integration,
            artifact_type=MCPCatalogArtifactType.RESOURCE,
            artifact_key="docs-readme-a1b2c3d4e7",
            artifact_ref="docs://readme",
            display_name="Readme",
        )

        service = MCPCatalogSearchService(session=session, role=org_admin_role)
        results = await service.search_catalog(
            workspace_id=workspace.id,
            query="list repos",
            limit=10,
        )

        assert [item.id for item in results.results[:2]] == [exact_entry, fuzzy_entry]
        assert results.results[0].rank >= results.results[1].rank

    async def test_search_catalog_filters_by_authorized_scopes_and_artifact_type(
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
            display_name="List repos",
        )
        await _insert_catalog_entry(
            session=session,
            integration=integration,
            artifact_type=MCPCatalogArtifactType.RESOURCE,
            artifact_key="docs-readme-a1b2c3d4e7",
            artifact_ref="docs://readme",
            display_name="Readme",
        )
        allowed_scope, _resource, _action = build_mcp_scope_name(
            scope_namespace=integration.scope_namespace,
            artifact_type=MCPCatalogArtifactType.TOOL,
            artifact_key="github-list-repos-a1b2c3d4e5",
        )
        role = member_role.model_copy(update={"scopes": frozenset({allowed_scope})})

        service = MCPCatalogSearchService(session=session, role=role)
        results = await service.search_catalog(
            workspace_id=workspace.id,
            query="repos",
            artifact_types=[MCPCatalogArtifactType.TOOL],
            limit=10,
        )

        assert [item.id for item in results.results] == [allowed_entry]
        assert results.results[0].scope_name == allowed_scope
