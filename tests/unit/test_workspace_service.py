"""Tests for WorkspaceService functionality."""

import uuid

import pytest
from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.schemas import UserRole
from tracecat.auth.types import Role
from tracecat.authz.scopes import ADMIN_SCOPES
from tracecat.db.models import Membership, Organization, User, Workspace
from tracecat.workspaces.schemas import (
    WorkspaceSettings,
    WorkspaceSettingsUpdate,
    WorkspaceUpdate,
)
from tracecat.workspaces.service import WorkspaceService

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
async def service(session: AsyncSession, svc_role: Role) -> WorkspaceService:
    """Create a workspace service instance for testing."""
    return WorkspaceService(session=session, role=svc_role)


@pytest.mark.anyio
class TestWorkspaceService:
    """Test WorkspaceService methods."""

    async def test_get_workspace_success(
        self, service: WorkspaceService, svc_workspace: Workspace
    ) -> None:
        """Test successfully retrieving a workspace by ID."""
        # Test retrieving the existing workspace
        workspace = await service.get_workspace(svc_workspace.id)

        assert workspace is not None
        assert workspace.id == svc_workspace.id
        assert workspace.name == svc_workspace.name
        assert isinstance(workspace, Workspace)

    async def test_get_workspace_not_found(self, service: WorkspaceService) -> None:
        """Test getting a workspace that doesn't exist returns None."""
        non_existent_id = uuid.uuid4()

        workspace = await service.get_workspace(non_existent_id)

        assert workspace is None

    async def test_get_workspace_validates_model(
        self, service: WorkspaceService, svc_workspace: Workspace
    ) -> None:
        """Test that get_workspace properly validates the model when loading from database."""
        # First, retrieve the workspace to ensure it exists
        workspace = await service.get_workspace(svc_workspace.id)

        assert workspace is not None
        # Verify that the workspace was properly validated by checking it's a Workspace instance
        assert isinstance(workspace, Workspace)
        # The model_validate call ensures Pydantic validation was invoked
        assert hasattr(workspace, "id")
        assert hasattr(workspace, "name")
        assert hasattr(workspace, "settings")
        # Verify that settings is properly validated as WorkspaceSettings instance
        ta = TypeAdapter(WorkspaceSettings)
        assert ta.validate_python(workspace.settings) is not None

    async def test_get_workspace_model_validation_preserves_data(
        self, service: WorkspaceService, svc_workspace: Workspace
    ) -> None:
        """Test that model validation preserves workspace data correctly."""
        workspace = await service.get_workspace(svc_workspace.id)

        assert workspace is not None
        assert workspace.id == svc_workspace.id
        assert workspace.name == svc_workspace.name
        assert workspace.organization_id == svc_workspace.organization_id
        # Verify settings are preserved through validation
        assert workspace.settings is not None

    async def test_delete_workspace_removes_memberships(
        self,
        session: AsyncSession,
        svc_organization: Organization,
    ) -> None:
        """Deleting a workspace should cascade to membership rows."""
        workspace = Workspace(
            name="test-workspace",
            organization_id=svc_organization.id,
        )
        other_workspace = Workspace(
            name="other-workspace",
            organization_id=svc_organization.id,
        )
        member = User(
            id=uuid.uuid4(),
            email=f"member-{uuid.uuid4().hex[:8]}@example.com",
            hashed_password="hashed",
            role=UserRole.BASIC,
            is_active=True,
            is_superuser=False,
            is_verified=True,
        )
        session.add_all([workspace, other_workspace, member])
        await session.flush()

        session.add(
            Membership(
                user_id=member.id,
                workspace_id=workspace.id,
            )
        )
        await session.commit()

        service = WorkspaceService(
            session=session,
            role=Role(
                type="user",
                workspace_id=workspace.id,
                organization_id=svc_organization.id,
                user_id=uuid.uuid4(),
                service_id="tracecat-api",
                scopes=ADMIN_SCOPES,
            ),
        )
        await service.delete_workspace(workspace.id)

        membership = await session.scalar(
            select(Membership).where(
                Membership.workspace_id == workspace.id,
                Membership.user_id == member.id,
            )
        )
        deleted_workspace = await session.scalar(
            select(Workspace).where(Workspace.id == workspace.id)
        )

        assert membership is None
        assert deleted_workspace is None

    async def test_update_workspace_merges_partial_settings(
        self,
        session: AsyncSession,
        service: WorkspaceService,
        svc_workspace: Workspace,
    ) -> None:
        """Partial settings updates should preserve unrelated keys."""
        svc_workspace.settings = {
            "git_repo_url": "git+ssh://git@github.com/acme/repo.git",
            "validate_attachment_magic_number": True,
        }
        session.add(svc_workspace)
        await session.commit()

        updated = await service.update_workspace(
            svc_workspace,
            WorkspaceUpdate(
                settings=WorkspaceSettingsUpdate(
                    workflow_unlimited_timeout_enabled=True
                )
            ),
        )

        assert updated.settings == {
            "git_repo_url": "git+ssh://git@github.com/acme/repo.git",
            "validate_attachment_magic_number": True,
            "workflow_unlimited_timeout_enabled": True,
        }

    async def test_update_workspace_preserves_other_settings_when_clearing_one_key(
        self,
        session: AsyncSession,
        service: WorkspaceService,
        svc_workspace: Workspace,
    ) -> None:
        """Explicit null updates should only clear the targeted setting key."""
        svc_workspace.settings = {
            "git_repo_url": "git+ssh://git@github.com/acme/repo.git",
            "workflow_default_timeout_seconds": 300,
        }
        session.add(svc_workspace)
        await session.commit()

        updated = await service.update_workspace(
            svc_workspace,
            WorkspaceUpdate(settings=WorkspaceSettingsUpdate(git_repo_url=None)),
        )

        assert updated.settings == {
            "git_repo_url": None,
            "workflow_default_timeout_seconds": 300,
        }

    async def test_update_workspace_replaces_list_settings_and_preserves_other_keys(
        self,
        session: AsyncSession,
        service: WorkspaceService,
        svc_workspace: Workspace,
    ) -> None:
        """A settings patch should replace list values and preserve unrelated keys."""
        svc_workspace.settings = {
            "git_repo_url": "git+ssh://git@github.com/acme/repo.git",
            "workflow_default_timeout_seconds": 300,
            "allowed_attachment_extensions": [".png"],
            "validate_attachment_magic_number": True,
        }
        session.add(svc_workspace)
        await session.commit()

        updated = await service.update_workspace(
            svc_workspace,
            WorkspaceUpdate(
                settings=WorkspaceSettingsUpdate(
                    git_repo_url=None,
                    workflow_unlimited_timeout_enabled=True,
                    allowed_attachment_extensions=[".pdf"],
                )
            ),
        )

        assert updated.settings == {
            "git_repo_url": None,
            "workflow_default_timeout_seconds": 300,
            "allowed_attachment_extensions": [".pdf"],
            "validate_attachment_magic_number": True,
            "workflow_unlimited_timeout_enabled": True,
        }

    async def test_update_workspace_allows_explicit_null_to_clear_settings(
        self,
        session: AsyncSession,
        service: WorkspaceService,
        svc_workspace: Workspace,
    ) -> None:
        """Explicit null settings updates should clear to an empty settings object."""
        svc_workspace.settings = {
            "git_repo_url": "git+ssh://git@github.com/acme/repo.git",
            "workflow_default_timeout_seconds": 300,
        }
        session.add(svc_workspace)
        await session.commit()

        updated = await service.update_workspace(
            svc_workspace,
            WorkspaceUpdate(settings=None),
        )

        assert updated.settings == {}


@pytest.mark.parametrize(
    "valid_url",
    [
        "git+ssh://git@github.com/org/repo.git",
        "git+ssh://git@gitlab.company.com:2222/team/project.git",
        "git+ssh://git@gitlab.com/group/subgroup/repo.git",
        "git+ssh://git@example.com/org/repo",
        "git+ssh://someuser@git.example.com/org/repo.git",
        "git+ssh://git@github.com/org/repo.git@feature/custom-branch",
    ],
)
def test_workspace_settings_update_accepts_valid_git_urls(valid_url: str) -> None:
    """Workspace settings should accept git+ssh URLs that match the shared regex."""
    settings = WorkspaceSettingsUpdate(git_repo_url=valid_url)

    assert settings.git_repo_url == valid_url


@pytest.mark.parametrize(
    "invalid_url",
    [
        "https://github.com/org/repo.git",
        "git+ssh://github.com/org/repo.git",
        "git+ssh://git@github.com",
        "git+ssh://git@github.com:not_a_port/org/repo.git",
        "git+ssh://git@github.com:/org/repo.git",
        "git+ssh://git@github.com/repo.git",
    ],
)
def test_workspace_settings_update_rejects_invalid_git_urls(invalid_url: str) -> None:
    """Workspace settings should reject malformed git repo URLs."""
    with pytest.raises(ValueError) as exc_info:
        WorkspaceSettingsUpdate(git_repo_url=invalid_url)

    assert "Must be a valid Git SSH URL" in str(exc_info.value)
