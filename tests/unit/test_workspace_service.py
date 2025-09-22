"""Tests for WorkspaceService functionality."""

import uuid

import pytest
from pydantic import TypeAdapter
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat import config
from tracecat.db.schemas import Workspace
from tracecat.types.auth import Role
from tracecat.workspaces.models import WorkspaceSettings
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
