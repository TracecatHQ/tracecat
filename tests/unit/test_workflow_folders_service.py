from collections.abc import AsyncGenerator

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.db.schemas import Workflow, Workspace
from tracecat.identifiers.workflow import WorkflowID
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatValidationError
from tracecat.workflow.management.folders.models import WorkflowFolderCreate
from tracecat.workflow.management.folders.service import WorkflowFolderService

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
async def folder_service(
    session: AsyncSession, svc_role: Role
) -> WorkflowFolderService:
    """Create a workflow folder service instance for testing."""
    return WorkflowFolderService(session=session, role=svc_role)


@pytest.fixture
def folder_create_params() -> WorkflowFolderCreate:
    """Sample folder creation parameters."""
    return WorkflowFolderCreate(
        name="test-folder",
        parent_path="/",
    )


@pytest.fixture
async def workflow_id(
    session: AsyncSession, svc_workspace: Workspace
) -> AsyncGenerator[WorkflowID, None]:
    """Create a test workflow in the database and return its ID."""
    # Create a test workflow
    workflow = Workflow(
        title="test-workflow",
        owner_id=svc_workspace.id,
        description="Test workflow for folders testing",
        status="active",
        entrypoint=None,
        returns=None,
        object=None,
    )  # type: ignore
    session.add(workflow)
    await session.commit()
    try:
        yield WorkflowID.new(workflow.id)
    finally:
        # Clean up the workflow after tests
        await session.delete(workflow)
        await session.commit()


@pytest.mark.anyio
class TestWorkflowFolderService:
    async def test_create_and_get_folder(
        self,
        folder_service: WorkflowFolderService,
        folder_create_params: WorkflowFolderCreate,
    ) -> None:
        """Test creating and retrieving a folder."""
        # Create folder
        created_folder = await folder_service.create_folder(
            name=folder_create_params.name, parent_path=folder_create_params.parent_path
        )
        assert created_folder.name == folder_create_params.name
        assert created_folder.path == f"/{folder_create_params.name}/"
        assert created_folder.owner_id == folder_service.workspace_id

        # Retrieve folder by ID
        retrieved_folder = await folder_service.get_folder(created_folder.id)
        assert retrieved_folder is not None
        assert retrieved_folder.id == created_folder.id
        assert retrieved_folder.name == folder_create_params.name
        assert retrieved_folder.path == f"/{folder_create_params.name}/"

        # Retrieve folder by path
        path_folder = await folder_service.get_folder_by_path(
            f"/{folder_create_params.name}/"
        )
        assert path_folder is not None
        assert path_folder.id == created_folder.id

    async def test_create_nested_folders(
        self, folder_service: WorkflowFolderService
    ) -> None:
        """Test creating nested folders."""
        # Create parent folder
        parent = await folder_service.create_folder(name="parent", parent_path="/")
        assert parent.path == "/parent/"

        # Create child folder
        child = await folder_service.create_folder(name="child", parent_path="/parent/")
        assert child.path == "/parent/child/"

        # Create grandchild folder
        grandchild = await folder_service.create_folder(
            name="grandchild", parent_path="/parent/child/"
        )
        assert grandchild.path == "/parent/child/grandchild/"

        # Verify paths through parent_path property
        assert parent.parent_path == "/"
        assert child.parent_path == "/parent/"
        assert grandchild.parent_path == "/parent/child/"

    async def test_list_folders(self, folder_service: WorkflowFolderService) -> None:
        """Test listing folders in a hierarchy."""
        # Create multiple folders
        await folder_service.create_folder(name="folder1", parent_path="/")
        await folder_service.create_folder(name="folder2", parent_path="/")

        # Create nested folders
        await folder_service.create_folder(name="parent", parent_path="/")
        await folder_service.create_folder(name="child1", parent_path="/parent/")
        await folder_service.create_folder(name="child2", parent_path="/parent/")

        # List all folders (the service now returns all matching folders in the subtree)
        all_folders = await folder_service.list_folders(parent_path="/")
        assert len(all_folders) == 5  # All 5 folders we created

        # List folders in /parent/ subtree
        parent_subtree = await folder_service.list_folders(parent_path="/parent/")
        assert len(parent_subtree) == 3  # parent, child1, child2

        # To get only root folders, we can filter the results
        root_folders = [
            f for f in all_folders if f.path.count("/") == 2
        ]  # Root folders have exactly 2 slashes
        assert len(root_folders) == 3
        root_names = {folder.name for folder in root_folders}
        assert {"folder1", "folder2", "parent"} == root_names

        # To get only direct children of parent, we can filter by path structure
        parent_children = [
            f
            for f in parent_subtree
            if f.path.count("/") == 3 and f.path.startswith("/parent/")
        ]
        assert len(parent_children) == 2
        child_names = {folder.name for folder in parent_children}
        assert {"child1", "child2"} == child_names

    async def test_rename_folder(self, folder_service: WorkflowFolderService) -> None:
        """Test renaming a folder and updating its path."""
        # Create a folder to rename
        folder = await folder_service.create_folder(name="old-name", parent_path="/")
        assert folder.path == "/old-name/"

        # Create a child folder to verify path updates
        child = await folder_service.create_folder(
            name="child", parent_path="/old-name/"
        )
        assert child.path == "/old-name/child/"

        # Rename the folder
        renamed = await folder_service.rename_folder(folder.id, "new-name")
        assert renamed.name == "new-name"
        assert renamed.path == "/new-name/"

        # Check that child path was updated
        updated_child = await folder_service.get_folder(child.id)
        assert updated_child is not None
        assert updated_child.path == "/new-name/child/"

    async def test_move_folder(self, folder_service: WorkflowFolderService) -> None:
        """Test moving a folder to a new parent."""
        # Create folders
        await folder_service.create_folder(name="parent1", parent_path="/")
        parent2 = await folder_service.create_folder(name="parent2", parent_path="/")
        child = await folder_service.create_folder(
            name="child", parent_path="/parent1/"
        )
        grandchild = await folder_service.create_folder(
            name="grandchild", parent_path="/parent1/child/"
        )

        # Get IDs to use for move operation
        parent2_id = parent2.id
        child_id = child.id

        # Move the child folder from parent1 to parent2
        moved = await folder_service.move_folder(child_id, parent2_id)
        assert moved.path == "/parent2/child/"

        # Check that grandchild path was updated
        updated_grandchild = await folder_service.get_folder(grandchild.id)
        assert updated_grandchild is not None
        assert updated_grandchild.path == "/parent2/child/grandchild/"

    async def test_folder_tree(self, folder_service: WorkflowFolderService) -> None:
        """Test retrieving folder tree."""
        # Create a folder hierarchy
        await folder_service.create_folder(name="folder1", parent_path="/")
        await folder_service.create_folder(name="parent", parent_path="/")
        await folder_service.create_folder(name="child1", parent_path="/parent/")
        await folder_service.create_folder(name="child2", parent_path="/parent/")
        await folder_service.create_folder(
            name="grandchild", parent_path="/parent/child1/"
        )

        # Get full tree
        tree = await folder_service.get_folder_tree()
        assert len(tree) == 5  # All folders

        # Get subtree
        subtree = await folder_service.get_folder_tree("/parent/")
        assert len(subtree) == 4  # parent, child1, child2, grandchild

        # Get deeper subtree
        deep_subtree = await folder_service.get_folder_tree("/parent/child1/")
        assert len(deep_subtree) == 2  # child1, grandchild

    async def test_delete_folder_non_recursive(
        self, folder_service: WorkflowFolderService
    ) -> None:
        """Test deleting an empty folder."""
        # Create a folder to delete
        folder = await folder_service.create_folder(name="to-delete", parent_path="/")

        # Delete the folder
        await folder_service.delete_folder(folder.id, recursive=False)

        # Verify deletion
        deleted = await folder_service.get_folder(folder.id)
        assert deleted is None

    async def test_delete_folder_with_children_fails(
        self, folder_service: WorkflowFolderService
    ) -> None:
        """Test deleting a folder with children without recursive flag fails."""
        # Create parent folder
        parent = await folder_service.create_folder(name="parent", parent_path="/")

        # Create child folder
        await folder_service.create_folder(name="child", parent_path="/parent/")

        # Try to delete parent non-recursively - should fail
        with pytest.raises(TracecatValidationError):
            await folder_service.delete_folder(parent.id, recursive=False)

        # Parent should still exist
        assert await folder_service.get_folder(parent.id) is not None

    async def test_delete_folder_recursive(
        self, folder_service: WorkflowFolderService
    ) -> None:
        """Test deleting a folder tree recursively."""
        # Create a folder hierarchy
        parent = await folder_service.create_folder(name="parent", parent_path="/")
        child = await folder_service.create_folder(name="child", parent_path="/parent/")
        grandchild = await folder_service.create_folder(
            name="grandchild", parent_path="/parent/child/"
        )

        # Store IDs for verification
        parent_id, child_id, grandchild_id = parent.id, child.id, grandchild.id

        # Delete parent recursively
        await folder_service.delete_folder(parent_id, recursive=True)

        # Verify all folders are deleted
        assert await folder_service.get_folder(parent_id) is None
        assert await folder_service.get_folder(child_id) is None
        assert await folder_service.get_folder(grandchild_id) is None

    async def test_workflows_in_folder(
        self,
        folder_service: WorkflowFolderService,
        workflow_id: WorkflowID,
        session: AsyncSession,
    ) -> None:
        """Test adding workflows to a folder and retrieving them."""
        # Create a folder
        folder = await folder_service.create_folder(name="test-folder", parent_path="/")

        # Move workflow to folder
        updated_workflow = await folder_service.move_workflow(workflow_id, folder)
        assert updated_workflow.folder_id == folder.id

        # Verify workflow is in folder
        workflows = await folder_service.get_workflows_in_folder(folder.id)
        assert len(workflows) == 1
        assert workflows[0].id == workflow_id

    async def test_move_workflow_between_folders(
        self, folder_service: WorkflowFolderService, workflow_id: WorkflowID
    ) -> None:
        """Test moving a workflow between folders."""
        # Create two folders
        folder1 = await folder_service.create_folder(name="folder1", parent_path="/")
        folder2 = await folder_service.create_folder(name="folder2", parent_path="/")

        # Move workflow to first folder
        workflow = await folder_service.move_workflow(workflow_id, folder1)
        assert workflow.folder_id == folder1.id

        # Move workflow to second folder
        workflow = await folder_service.move_workflow(workflow_id, folder2)
        assert workflow.folder_id == folder2.id

        # Verify workflow is in the second folder
        workflows_in_folder1 = await folder_service.get_workflows_in_folder(folder1.id)
        assert len(workflows_in_folder1) == 0

        workflows_in_folder2 = await folder_service.get_workflows_in_folder(folder2.id)
        assert len(workflows_in_folder2) == 1
        assert workflows_in_folder2[0].id == workflow_id
