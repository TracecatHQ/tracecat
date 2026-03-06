"""Unit tests for RBAC service."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_ee.rbac.service import RBACService

from tracecat.auth.types import Role
from tracecat.authz.enums import ScopeSource
from tracecat.authz.scopes import ORG_ADMIN_SCOPES
from tracecat.authz.seeding import seed_system_scopes
from tracecat.db.models import (
    MCPIntegration,
    MCPIntegrationCatalogEntry,
    Organization,
    OrganizationMembership,
    RoleScope,
    Scope,
    User,
    Workspace,
)
from tracecat.exceptions import (
    TracecatAuthorizationError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.integrations.enums import MCPAuthType, MCPCatalogArtifactType


@pytest.fixture
async def org(session: AsyncSession) -> Organization:
    """Create a test organization."""
    org_id = uuid.uuid4()
    org = Organization(id=org_id, name="Test Org", slug=f"test-org-{org_id.hex[:8]}")
    session.add(org)
    await session.commit()
    await session.refresh(org)
    return org


@pytest.fixture
async def user(session: AsyncSession, org: Organization) -> User:
    """Create a test user with org membership."""
    user = User(
        id=uuid.uuid4(),
        email="test@example.com",
        hashed_password="test",
    )
    session.add(user)
    await session.flush()

    # Add org membership
    membership = OrganizationMembership(
        user_id=user.id,
        organization_id=org.id,
    )
    session.add(membership)
    await session.commit()
    await session.refresh(user)
    return user


@pytest.fixture
async def workspace(session: AsyncSession, org: Organization) -> Workspace:
    """Create a test workspace."""
    workspace = Workspace(
        id=uuid.uuid4(),
        name="Test Workspace",
        organization_id=org.id,
    )
    session.add(workspace)
    await session.commit()
    await session.refresh(workspace)
    return workspace


@pytest.fixture
async def seeded_scopes(session: AsyncSession) -> list[Scope]:
    """Seed system scopes and return them."""
    await seed_system_scopes(session)
    result = await session.execute(
        select(Scope).where(Scope.source == ScopeSource.PLATFORM)
    )
    return list(result.scalars().all())


@pytest.fixture
def role(org: Organization, user: User) -> Role:
    """Create a test role for the service."""
    return Role(
        type="user",
        user_id=user.id,
        organization_id=org.id,
        service_id="tracecat-api",
        scopes=ORG_ADMIN_SCOPES,
    )


async def _create_mcp_integration(
    *,
    session: AsyncSession,
    workspace: Workspace,
    name: str = "GitHub MCP",
    scope_namespace: str = "mcpnamespace0001",
) -> MCPIntegration:
    """Insert a test MCP integration for RBAC sync coverage."""
    integration = MCPIntegration(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        name=name,
        description="Test MCP integration",
        slug=f"{name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:6]}",
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
    display_name: str,
    description: str | None,
    is_active: bool = True,
) -> None:
    """Insert a persisted MCP catalog entry for RBAC sync coverage."""
    await session.execute(
        insert(MCPIntegrationCatalogEntry).values(
            id=uuid.uuid4(),
            mcp_integration_id=integration.id,
            workspace_id=integration.workspace_id,
            integration_name=integration.name,
            artifact_type=artifact_type.value,
            artifact_key=artifact_key,
            artifact_ref=artifact_ref,
            display_name=display_name,
            description=description,
            input_schema={"type": "object"},
            artifact_metadata={"origin": "test"},
            raw_payload={"name": artifact_ref},
            content_hash=artifact_key.ljust(64, "0"),
            is_active=is_active,
            search_vector=func.to_tsvector("simple", artifact_ref),
        )
    )
    await session.commit()


@pytest.mark.anyio
class TestRBACServiceScopes:
    """Test scope management in RBAC service."""

    async def test_list_scopes_with_system_scopes(
        self,
        session: AsyncSession,
        role: Role,
        seeded_scopes: list[Scope],
    ):
        """List scopes should include system scopes."""
        service = RBACService(session, role=role)
        scopes = await service.list_scopes(include_system=True)
        assert len(scopes) > 0
        # All system scopes should be included
        system_scope_names = {s.name for s in seeded_scopes}
        returned_names = {s.name for s in scopes}
        assert system_scope_names.issubset(returned_names)

    async def test_list_scopes_filter_by_source(
        self,
        session: AsyncSession,
        role: Role,
        seeded_scopes: list[Scope],
    ):
        """List scopes can filter by source."""
        service = RBACService(session, role=role)
        scopes = await service.list_scopes(
            include_system=True, source=ScopeSource.PLATFORM
        )
        assert all(s.source == ScopeSource.PLATFORM for s in scopes)

    async def test_create_custom_scope(
        self,
        session: AsyncSession,
        role: Role,
        org: Organization,
    ):
        """Create a custom scope."""
        service = RBACService(session, role=role)
        scope = await service.create_scope(
            name="custom:test",
            description="A test custom scope",
        )
        assert scope.name == "custom:test"
        assert scope.resource == "custom"
        assert scope.action == "test"
        assert scope.source == ScopeSource.CUSTOM
        assert scope.organization_id == org.id

    async def test_create_scope_invalid_format(
        self,
        session: AsyncSession,
        role: Role,
    ):
        """Creating scope with invalid format should fail."""
        service = RBACService(session, role=role)
        with pytest.raises(TracecatValidationError):
            await service.create_scope(name="INVALID SCOPE")

    async def test_delete_custom_scope(
        self,
        session: AsyncSession,
        role: Role,
    ):
        """Delete a custom scope."""
        service = RBACService(session, role=role)
        scope = await service.create_scope(name="custom:delete-me")
        await service.delete_scope(scope.id)

        with pytest.raises(TracecatNotFoundError):
            await service.get_scope(scope.id)

    async def test_delete_system_scope_fails(
        self,
        session: AsyncSession,
        role: Role,
        seeded_scopes: list[Scope],
    ):
        """Cannot delete system scopes."""
        service = RBACService(session, role=role)
        system_scope = seeded_scopes[0]

        with pytest.raises(TracecatAuthorizationError):
            await service.delete_scope(system_scope.id)

    async def test_sync_mcp_integration_scopes_creates_custom_scopes(
        self,
        session: AsyncSession,
        role: Role,
        org: Organization,
        workspace: Workspace,
    ) -> None:
        """Active MCP catalog entries should map to org-scoped custom scopes."""
        integration = await _create_mcp_integration(
            session=session,
            workspace=workspace,
            scope_namespace="syncscope0000001",
        )
        await _insert_catalog_entry(
            session=session,
            integration=integration,
            artifact_type=MCPCatalogArtifactType.TOOL,
            artifact_key="github-list-repos-a1b2c3d4e5",
            artifact_ref="github.list_repos",
            display_name="List repos",
            description="List GitHub repositories",
        )
        await _insert_catalog_entry(
            session=session,
            integration=integration,
            artifact_type=MCPCatalogArtifactType.RESOURCE,
            artifact_key="docs-readme-7f8e9d0c1b",
            artifact_ref="docs://README",
            display_name="README",
            description="Read the repository README",
        )
        await _insert_catalog_entry(
            session=session,
            integration=integration,
            artifact_type=MCPCatalogArtifactType.PROMPT,
            artifact_key="triage-incident-6a5b4c3d2e",
            artifact_ref="triage_incident",
            display_name="Triage incident",
            description="Guide incident triage",
        )

        service = RBACService(session, role=role)
        scopes = await service.sync_mcp_integration_scopes(
            mcp_integration_id=integration.id
        )

        assert [scope.name for scope in scopes] == [
            "mcp-prompt:syncscope0000001.triage-incident-6a5b4c3d2e:use",
            "mcp-resource:syncscope0000001.docs-readme-7f8e9d0c1b:read",
            "mcp-tool:syncscope0000001.github-list-repos-a1b2c3d4e5:execute",
        ]
        assert {scope.resource for scope in scopes} == {
            "mcp-tool",
            "mcp-resource",
            "mcp-prompt",
        }
        assert {scope.action for scope in scopes} == {"execute", "read", "use"}
        assert all(scope.source == ScopeSource.CUSTOM for scope in scopes)
        assert all(scope.organization_id == org.id for scope in scopes)
        assert {scope.source_ref for scope in scopes} == {
            f"mcp:{integration.id}:tool:github-list-repos-a1b2c3d4e5",
            f"mcp:{integration.id}:resource:docs-readme-7f8e9d0c1b",
            f"mcp:{integration.id}:prompt:triage-incident-6a5b4c3d2e",
        }

    async def test_sync_mcp_integration_scopes_updates_and_deletes_removed_entries(
        self,
        session: AsyncSession,
        role: Role,
        workspace: Workspace,
    ) -> None:
        """Resync should update descriptions and delete removed MCP scopes."""
        integration = await _create_mcp_integration(
            session=session,
            workspace=workspace,
            scope_namespace="syncscope0000002",
        )
        await _insert_catalog_entry(
            session=session,
            integration=integration,
            artifact_type=MCPCatalogArtifactType.TOOL,
            artifact_key="github-list-repos-a1b2c3d4e5",
            artifact_ref="github.list_repos",
            display_name="List repos",
            description="Initial tool description",
        )
        await _insert_catalog_entry(
            session=session,
            integration=integration,
            artifact_type=MCPCatalogArtifactType.PROMPT,
            artifact_key="triage-incident-6a5b4c3d2e",
            artifact_ref="triage_incident",
            display_name="Triage incident",
            description="Initial prompt description",
        )

        service = RBACService(session, role=role)
        scopes = await service.sync_mcp_integration_scopes(
            mcp_integration_id=integration.id
        )
        tool_scope = next(scope for scope in scopes if scope.resource == "mcp-tool")
        role_with_scope = await service.create_role(
            name="MCP Operator",
            scope_ids=[tool_scope.id],
        )

        tool_entry_stmt = select(MCPIntegrationCatalogEntry).where(
            MCPIntegrationCatalogEntry.mcp_integration_id == integration.id,
            MCPIntegrationCatalogEntry.artifact_type
            == MCPCatalogArtifactType.TOOL.value,
        )
        tool_entry = (await session.execute(tool_entry_stmt)).scalar_one()
        tool_entry.is_active = False

        prompt_entry_stmt = select(MCPIntegrationCatalogEntry).where(
            MCPIntegrationCatalogEntry.mcp_integration_id == integration.id,
            MCPIntegrationCatalogEntry.artifact_type
            == MCPCatalogArtifactType.PROMPT.value,
        )
        prompt_entry = (await session.execute(prompt_entry_stmt)).scalar_one()
        prompt_entry.description = "Updated prompt description"
        session.add_all([tool_entry, prompt_entry])
        await session.commit()

        updated_scopes = await service.sync_mcp_integration_scopes(
            mcp_integration_id=integration.id
        )

        assert [scope.name for scope in updated_scopes] == [
            "mcp-prompt:syncscope0000002.triage-incident-6a5b4c3d2e:use"
        ]
        assert updated_scopes[0].description is not None
        assert "Updated prompt description" in updated_scopes[0].description

        removed_scope = await session.execute(
            select(Scope).where(Scope.id == tool_scope.id)
        )
        assert removed_scope.scalar_one_or_none() is None

        role_scope_count = await session.execute(
            select(func.count())
            .select_from(RoleScope)
            .where(RoleScope.role_id == role_with_scope.id)
        )
        assert role_scope_count.scalar_one() == 0


@pytest.mark.anyio
class TestRBACServiceRoles:
    """Test role management in RBAC service."""

    async def test_create_custom_role(
        self,
        session: AsyncSession,
        role: Role,
        org: Organization,
    ):
        """Create a custom role."""
        service = RBACService(session, role=role)
        custom_role = await service.create_role(
            name="Security Analyst",
            description="A custom security analyst role",
        )
        assert custom_role.name == "Security Analyst"
        assert custom_role.organization_id == org.id
        assert custom_role.created_by == role.user_id

    async def test_create_role_with_scopes(
        self,
        session: AsyncSession,
        role: Role,
        seeded_scopes: list[Scope],
    ):
        """Create a role with scopes assigned."""
        service = RBACService(session, role=role)
        scope_ids = [s.id for s in seeded_scopes[:3]]

        custom_role = await service.create_role(
            name="Custom Role With Scopes",
            scope_ids=scope_ids,
        )
        assert len(custom_role.scopes) == 3

    async def test_update_role(
        self,
        session: AsyncSession,
        role: Role,
    ):
        """Update a custom role."""
        service = RBACService(session, role=role)
        custom_role = await service.create_role(name="Original Name")

        updated = await service.update_role(
            custom_role.id,
            name="Updated Name",
            description="New description",
        )
        assert updated.name == "Updated Name"
        assert updated.description == "New description"

    async def test_delete_role(
        self,
        session: AsyncSession,
        role: Role,
    ):
        """Delete a custom role."""
        service = RBACService(session, role=role)
        custom_role = await service.create_role(name="To Delete")
        await service.delete_role(custom_role.id)

        with pytest.raises(TracecatNotFoundError):
            await service.get_role(custom_role.id)

    async def test_delete_role_with_assignments_fails(
        self,
        session: AsyncSession,
        role: Role,
        org: Organization,
    ):
        """Cannot delete role that has active assignments."""
        service = RBACService(session, role=role)

        # Create role and group
        custom_role = await service.create_role(name="Assigned Role")
        group = await service.create_group(name="Test Group")

        # Create assignment
        await service.create_group_role_assignment(
            group_id=group.id,
            role_id=custom_role.id,
        )

        # Try to delete - should fail
        with pytest.raises(TracecatValidationError):
            await service.delete_role(custom_role.id)


@pytest.mark.anyio
class TestRBACServiceGroups:
    """Test group management in RBAC service."""

    async def test_create_group(
        self,
        session: AsyncSession,
        role: Role,
        org: Organization,
    ):
        """Create a group."""
        service = RBACService(session, role=role)
        group = await service.create_group(
            name="Engineering Team",
            description="The engineering team",
        )
        assert group.name == "Engineering Team"
        assert group.organization_id == org.id
        assert group.created_by == role.user_id

    async def test_add_member_to_group(
        self,
        session: AsyncSession,
        role: Role,
        user: User,
    ):
        """Add a member to a group."""
        service = RBACService(session, role=role)
        group = await service.create_group(name="Test Group")

        await service.add_group_member(group.id, user.id)

        members = await service.list_group_members(group.id)
        assert len(members) == 1
        assert members[0][0].id == user.id

    async def test_add_duplicate_member_fails(
        self,
        session: AsyncSession,
        role: Role,
        user: User,
    ):
        """Adding same member twice should fail."""
        service = RBACService(session, role=role)
        group = await service.create_group(name="Test Group")

        await service.add_group_member(group.id, user.id)

        with pytest.raises(TracecatValidationError):
            await service.add_group_member(group.id, user.id)

    async def test_remove_member_from_group(
        self,
        session: AsyncSession,
        role: Role,
        user: User,
    ):
        """Remove a member from a group."""
        service = RBACService(session, role=role)
        group = await service.create_group(name="Test Group")

        await service.add_group_member(group.id, user.id)
        await service.remove_group_member(group.id, user.id)

        members = await service.list_group_members(group.id)
        assert len(members) == 0


@pytest.mark.anyio
class TestRBACServiceAssignments:
    """Test group assignment management."""

    async def test_create_org_wide_assignment(
        self,
        session: AsyncSession,
        role: Role,
        org: Organization,
    ):
        """Create an org-wide assignment."""
        service = RBACService(session, role=role)

        custom_role = await service.create_role(name="Test Role")
        group = await service.create_group(name="Test Group")

        assignment = await service.create_group_role_assignment(
            group_id=group.id,
            role_id=custom_role.id,
            workspace_id=None,  # Org-wide
        )

        assert assignment.organization_id == org.id
        assert assignment.workspace_id is None
        assert assignment.role_id == custom_role.id

    async def test_create_workspace_assignment(
        self,
        session: AsyncSession,
        role: Role,
        workspace: Workspace,
    ):
        """Create a workspace-specific assignment."""
        service = RBACService(session, role=role)

        custom_role = await service.create_role(name="Test Role")
        group = await service.create_group(name="Test Group")

        assignment = await service.create_group_role_assignment(
            group_id=group.id,
            role_id=custom_role.id,
            workspace_id=workspace.id,
        )

        assert assignment.workspace_id == workspace.id

    async def test_update_assignment(
        self,
        session: AsyncSession,
        role: Role,
    ):
        """Update an assignment's role."""
        service = RBACService(session, role=role)

        role1 = await service.create_role(name="Role 1")
        role2 = await service.create_role(name="Role 2")
        group = await service.create_group(name="Test Group")

        assignment = await service.create_group_role_assignment(
            group_id=group.id,
            role_id=role1.id,
        )

        updated = await service.update_group_role_assignment(
            assignment.id,
            role_id=role2.id,
        )

        assert updated.role_id == role2.id


@pytest.mark.anyio
class TestRBACServiceUserAssignments:
    """Test direct user role assignment management."""

    async def test_create_user_assignment_for_org_member(
        self,
        session: AsyncSession,
        role: Role,
        user: User,
    ):
        """Create direct assignment for org member."""
        service = RBACService(session, role=role)
        custom_role = await service.create_role(name="Direct User Role")

        assignment = await service.create_user_assignment(
            user_id=user.id,
            role_id=custom_role.id,
        )

        assert assignment.user_id == user.id
        assert assignment.role_id == custom_role.id
        assert assignment.organization_id == role.organization_id

    async def test_create_user_assignment_rejects_non_member(
        self,
        session: AsyncSession,
        role: Role,
    ):
        """Cannot assign org role to user outside organization."""
        service = RBACService(session, role=role)
        custom_role = await service.create_role(name="Direct User Role")

        external_user = User(
            id=uuid.uuid4(),
            email="external@example.com",
            hashed_password="test",
        )
        session.add(external_user)
        await session.commit()

        with pytest.raises(
            TracecatNotFoundError, match="User not found in organization"
        ):
            await service.create_user_assignment(
                user_id=external_user.id,
                role_id=custom_role.id,
            )


@pytest.mark.anyio
class TestRBACServiceScopeComputation:
    """Test scope computation from group memberships."""

    async def test_get_group_scopes_empty(
        self,
        session: AsyncSession,
        role: Role,
        user: User,
    ):
        """User with no group memberships has no group scopes."""
        service = RBACService(session, role=role)
        scopes = await service.get_group_scopes(user.id)
        assert scopes == frozenset()

    async def test_get_group_scopes_with_assignment(
        self,
        session: AsyncSession,
        role: Role,
        user: User,
        seeded_scopes: list[Scope],
    ):
        """User gets scopes from group membership."""
        service = RBACService(session, role=role)

        # Create role with scopes
        scope_ids = [s.id for s in seeded_scopes[:2]]
        custom_role = await service.create_role(
            name="Test Role",
            scope_ids=scope_ids,
        )

        # Create group and add user
        group = await service.create_group(name="Test Group")
        await service.add_group_member(group.id, user.id)

        # Create assignment
        await service.create_group_role_assignment(
            group_id=group.id,
            role_id=custom_role.id,
        )

        # Get scopes
        scopes = await service.get_group_scopes(user.id)
        expected_names = {seeded_scopes[0].name, seeded_scopes[1].name}
        assert scopes == expected_names

    async def test_get_group_scopes_workspace_specific(
        self,
        session: AsyncSession,
        role: Role,
        user: User,
        workspace: Workspace,
        seeded_scopes: list[Scope],
    ):
        """Workspace-specific assignments only apply when workspace matches."""
        service = RBACService(session, role=role)

        # Create role with scopes
        custom_role = await service.create_role(
            name="Workspace Role",
            scope_ids=[seeded_scopes[0].id],
        )

        # Create group, add user, and assign to specific workspace
        group = await service.create_group(name="Test Group")
        await service.add_group_member(group.id, user.id)
        await service.create_group_role_assignment(
            group_id=group.id,
            role_id=custom_role.id,
            workspace_id=workspace.id,
        )

        # Without workspace context, no scopes
        scopes_no_ws = await service.get_group_scopes(user.id, workspace_id=None)
        assert scopes_no_ws == frozenset()

        # With matching workspace, get scopes
        scopes_with_ws = await service.get_group_scopes(
            user.id, workspace_id=workspace.id
        )
        assert seeded_scopes[0].name in scopes_with_ws

    async def test_get_group_scopes_org_wide_applies_to_workspace(
        self,
        session: AsyncSession,
        role: Role,
        user: User,
        workspace: Workspace,
        seeded_scopes: list[Scope],
    ):
        """Org-wide assignments apply even when workspace is specified."""
        service = RBACService(session, role=role)

        # Create role with scopes
        custom_role = await service.create_role(
            name="Org Role",
            scope_ids=[seeded_scopes[0].id],
        )

        # Create group, add user, and assign org-wide
        group = await service.create_group(name="Test Group")
        await service.add_group_member(group.id, user.id)
        await service.create_group_role_assignment(
            group_id=group.id,
            role_id=custom_role.id,
            workspace_id=None,  # Org-wide
        )

        # With workspace context, org-wide scopes still apply
        scopes = await service.get_group_scopes(user.id, workspace_id=workspace.id)
        assert seeded_scopes[0].name in scopes
