"""Tests for WorkspaceService functionality."""

import uuid

import pytest
from pydantic import TypeAdapter
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat import config
from tracecat.db.schemas import Workspace
from tracecat.types.auth import Role
from tracecat.workspaces.models import WorkspaceSettings, WorkspaceSettingsUpdate
from tracecat.workspaces.service import WorkspaceService

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
async def service(session: AsyncSession, svc_role: Role) -> WorkspaceService:
    """Create a workspace service instance for testing."""
    return WorkspaceService(session=session, role=svc_role)


@pytest.fixture
async def svc_workspace(session: AsyncSession) -> Workspace:
    """Create a workspace for testing."""

    workspace = Workspace(
        name="test-workspace", owner_id=config.TRACECAT__DEFAULT_ORG_ID
    )
    session.add(workspace)
    await session.commit()
    await session.refresh(workspace)
    return workspace


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
        assert workspace.owner_id == svc_workspace.owner_id
        # Verify settings are preserved through validation
        assert workspace.settings is not None


@pytest.mark.parametrize(
    "valid_url",
    [
        "git+ssh://git@github.com/org/repo.git",
        "git+ssh://git@gitlab.company.com:2222/team/project.git",
        "git+ssh://git@gitlab.com/group/subgroup/repo.git",
        "git+ssh://git@example.com/org/repo",
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
        "git+ssh://user@github.com/org/repo.git",
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
