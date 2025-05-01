from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlmodel import col, func, or_, select

from tracecat.db.schemas import Workflow, WorkflowFolder
from tracecat.identifiers import WorkflowID
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.service import BaseService
from tracecat.tags.models import TagRead
from tracecat.types.auth import Role
from tracecat.types.exceptions import (
    TracecatAuthorizationError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.workflow.management.folders.models import (
    DirectoryItem,
    FolderDirectoryItem,
    WorkflowDirectoryItem,
)


class WorkflowFolderService(BaseService):
    """Service for managing workflow folders using materialized path pattern."""

    service_name = "workflow_folders"

    def __init__(self, session, role: Role | None = None):
        super().__init__(session, role)
        if self.role.workspace_id is None:
            raise TracecatAuthorizationError("Workspace ID is required")
        self.workspace_id = self.role.workspace_id

    async def create_folder(self, name: str, parent_path: str = "/") -> WorkflowFolder:
        """Create a new workflow folder.

        Args:
            name: The name of the folder
            parent_path: Path of the parent folder, defaults to root

        Returns:
            The created folder
        """
        # Validate name - no slashes allowed
        if "/" in name:
            raise TracecatValidationError("Folder name cannot contain slashes")

        # Ensure parent path exists if not root
        if parent_path != "/":
            # We want to create the nested folders if not
            parent_exists = await self._folder_path_exists(parent_path)
            if not parent_exists:
                raise TracecatValidationError(f"Parent path {parent_path} not found")

        # Ensure parent_path has trailing slash
        if not parent_path.endswith("/"):
            parent_path += "/"

        # Create full path
        full_path = f"{parent_path}{name}/" if parent_path != "/" else f"/{name}/"

        # Check if path already exists
        path_exists = await self._folder_path_exists(full_path)
        if path_exists:
            raise TracecatValidationError(f"Folder {full_path} already exists")

        folder = WorkflowFolder(
            name=name,
            path=full_path,
            owner_id=self.workspace_id,
        )
        self.session.add(folder)
        await self.session.commit()
        await self.session.refresh(folder)
        return folder

    async def get_folder(self, folder_id: uuid.UUID) -> WorkflowFolder | None:
        """Get a folder by ID.

        Args:
            folder_id: The ID of the folder

        Returns:
            The folder or None if not found
        """
        statement = select(WorkflowFolder).where(
            WorkflowFolder.owner_id == self.workspace_id,
            WorkflowFolder.id == folder_id,
        )
        result = await self.session.exec(statement)
        return result.one_or_none()

    async def get_folder_by_path(self, path: str) -> WorkflowFolder | None:
        """Get a folder by its path.

        Args:
            path: The path of the folder

        Returns:
            The folder or None if not found
        """
        # Ensure path has trailing slash
        if not path.endswith("/") and path != "/":
            path += "/"

        statement = select(WorkflowFolder).where(
            WorkflowFolder.owner_id == self.workspace_id,
            WorkflowFolder.path == path,
        )
        result = await self.session.exec(statement)
        return result.one_or_none()

    async def list_folders(self, parent_path: str = "/") -> Sequence[WorkflowFolder]:
        """List all folders within the specified parent path subtree, or all folders if no path is specified.

        Args:
            parent_path: Optional path of the parent folder. If None or "/", lists all folders.
                         Otherwise, lists all folders whose path starts with parent_path.

        Returns:
            List of folders within the specified subtree or all folders.
        """
        # Base statement selecting folders for the current workspace

        statement = select(WorkflowFolder).where(
            WorkflowFolder.owner_id == self.workspace_id,
            col(WorkflowFolder.path).like(f"{parent_path}%"),
        )

        # Execute the query and return all matching folders
        result = await self.session.exec(statement)
        return result.all()

    async def get_workflows_in_folder(
        self, folder_id: uuid.UUID | None = None
    ) -> Sequence[Workflow]:
        """Get workflows in the specified folder.

        If folder_id is None, returns workflows not in any folder.

        Args:
            folder_id: Optional ID of the folder

        Returns:
            List of workflows in the folder
        """
        statement = select(Workflow).where(
            Workflow.owner_id == self.workspace_id,
            Workflow.folder_id == folder_id,
        )
        result = await self.session.exec(statement)
        return result.all()

    async def move_workflow(
        self, workflow_id: WorkflowID, folder: WorkflowFolder | None = None
    ) -> Workflow:
        """Move a workflow to a different folder.

        Args:
            workflow_id: ID of the workflow to move
            folder_id: ID of the destination folder

        Returns:
            The updated workflow
        """
        # Update the workflow
        statement = select(Workflow).where(
            Workflow.owner_id == self.workspace_id,
            Workflow.id == workflow_id,
        )
        result = await self.session.exec(statement)
        workflow = result.one_or_none()
        if not workflow:
            raise TracecatNotFoundError(f"Workflow {workflow_id} not found")

        workflow.folder_id = folder.id if folder else None

        self.session.add(workflow)
        await self.session.commit()
        await self.session.refresh(workflow)
        return workflow

    async def rename_folder(
        self, folder_id: uuid.UUID, new_name: str
    ) -> WorkflowFolder:
        """Rename a folder. Updates the folder name and path.

        Args:
            folder_id: ID of the folder to rename
            new_name: New name for the folder

        Returns:
            The updated folder
        """
        # Validate name - no slashes allowed
        if "/" in new_name:
            raise TracecatValidationError("Folder name cannot contain slashes")

        folder = await self.get_folder(folder_id)
        if not folder:
            raise TracecatValidationError(f"Folder {folder_id} not found")

        old_path = folder.path
        parent_path = folder.parent_path

        # Create the new path
        new_path = (
            f"{parent_path}{new_name}/" if parent_path != "/" else f"/{new_name}/"
        )

        # Check if new path already exists
        if new_path != old_path:
            path_exists = await self._folder_path_exists(new_path)
            if not path_exists:
                # TODO: Create the folder
                pass
            # if path_exists:
            #     raise TracecatValidationError(f"Folder {new_path} already exists")

        # Get all descendants to update their paths
        descendants = await self._get_descendants(old_path)

        # Update this folder
        folder.name = new_name
        folder.path = new_path
        self.session.add(folder)

        # Update all descendant paths
        for descendant in descendants:
            # Replace the old path prefix with the new one
            descendant.path = descendant.path.replace(old_path, new_path, 1)
            self.session.add(descendant)

        await self.session.commit()
        await self.session.refresh(folder)
        return folder

    async def move_folder(
        self, folder_id: uuid.UUID, new_parent_id: uuid.UUID | None
    ) -> WorkflowFolder:
        """Move a folder to a different parent.

        Args:
            folder_id: ID of the folder to move
            new_parent_id: ID of the new parent folder, or None for root

        Returns:
            The updated folder
        """
        folder = await self.get_folder(folder_id)
        if not folder:
            raise TracecatValidationError(f"Folder {folder_id} not found")

        # Determine new parent path
        new_parent_path = "/"
        if new_parent_id is not None:
            new_parent = await self.get_folder(new_parent_id)
            if not new_parent:
                raise TracecatValidationError(
                    f"Parent folder {new_parent_id} not found"
                )
            new_parent_path = new_parent.path

            # Check if we're trying to make a folder its own descendant
            if folder.path == new_parent_path:
                raise TracecatValidationError("Cannot make a folder its own child")

            # Check if new parent is a descendant of the folder (would create a cycle)
            if new_parent.path.startswith(folder.path):
                raise TracecatValidationError("Cannot create cyclic folder structure")

        old_path = folder.path
        old_name = folder.name

        # Create the new path
        new_path = (
            f"{new_parent_path}{old_name}/"
            if new_parent_path != "/"
            else f"/{old_name}/"
        )

        # Check if new path already exists
        if new_path != old_path:
            path_exists = await self._folder_path_exists(new_path)
            if path_exists:
                raise TracecatValidationError(f"Folder {new_path} already exists")

        # Get all descendants to update their paths
        descendants = await self._get_descendants(old_path)

        # Update this folder
        folder.path = new_path
        self.session.add(folder)

        # Update all descendant paths
        for descendant in descendants:
            # Replace the old path prefix with the new one
            descendant.path = descendant.path.replace(old_path, new_path, 1)
            self.session.add(descendant)

        await self.session.commit()
        await self.session.refresh(folder)
        return folder

    async def delete_folder(
        self, folder_id: uuid.UUID, recursive: bool = False
    ) -> None:
        """Delete a folder.

        Args:
            folder_id: ID of the folder to delete
            recursive: If True, also delete subfolders and contained workflows
                      If False, will fail if folder has children or workflows
        """
        folder = await self.get_folder(folder_id)
        if not folder:
            raise TracecatValidationError(f"Folder {folder_id} not found")

        # Prevent deletion of root folder
        if folder.path == "/":
            raise TracecatValidationError("Cannot delete root folder")

        if not recursive:
            # Check if folder has children or workflows
            has_children = await self._has_children(folder.path)
            has_workflows = await self._has_workflows(folder_id)

            if has_children or has_workflows:
                raise TracecatValidationError(
                    "Folder is not empty. Please move or delete its contents first."
                )
        else:
            # If recursive, delete all subfolders first
            descendants = await self._get_descendants(folder.path)
            for descendant in descendants:
                # Delete workflows in each subfolder
                statement = select(Workflow).where(
                    Workflow.owner_id == self.workspace_id,
                    Workflow.folder_id == descendant.id,
                )
                result = await self.session.exec(statement)
                for workflow in result:
                    workflow.folder_id = None
                    self.session.add(workflow)

                # Delete the subfolder
                await self.session.delete(descendant)

            # Delete workflows in the main folder
            statement = select(Workflow).where(
                Workflow.owner_id == self.workspace_id, Workflow.folder_id == folder.id
            )
            result = await self.session.exec(statement)
            for workflow in result:
                workflow.folder_id = None
                self.session.add(workflow)

        # Delete the folder
        await self.session.delete(folder)
        await self.session.commit()

    async def get_folder_tree(self, root_path: str = "/") -> Sequence[WorkflowFolder]:
        """Get the full folder tree starting from the given root path.

        Args:
            root_path: Path of the root folder, defaults to "/"

        Returns:
            List of folders in the tree
        """
        # Ensure root_path has trailing slash
        if not root_path.endswith("/") and root_path != "/":
            root_path += "/"

        statement = (
            select(WorkflowFolder)
            .where(
                WorkflowFolder.owner_id == self.workspace_id,
                or_(
                    col(WorkflowFolder.path).startswith(root_path),
                    col(WorkflowFolder.path) == root_path,
                ),
            )
            .order_by(WorkflowFolder.path)
        )

        result = await self.session.exec(statement)
        return result.all()

    async def _folder_path_exists(self, path: str) -> bool:
        """Check if a folder path exists."""
        statement = (
            select(func.count())
            .select_from(WorkflowFolder)
            .where(
                WorkflowFolder.owner_id == self.workspace_id,
                WorkflowFolder.path == path,
            )
        )
        result = await self.session.exec(statement)
        return result.one() > 0

    async def _has_children(self, path: str) -> bool:
        """Check if a folder has any child folders."""
        # Ensure path has trailing slash
        if not path.endswith("/") and path != "/":
            path += "/"

        statement = (
            select(func.count())
            .select_from(WorkflowFolder)
            .where(
                WorkflowFolder.owner_id == self.workspace_id,
                col(WorkflowFolder.path).startswith(path),
                col(WorkflowFolder.path) != path,
            )
        )
        result = await self.session.exec(statement)
        return result.one() > 0

    async def _has_workflows(self, folder_id: uuid.UUID) -> bool:
        """Check if a folder contains any workflows."""
        statement = (
            select(func.count())
            .select_from(Workflow)
            .where(
                Workflow.owner_id == self.workspace_id,
                Workflow.folder_id == folder_id,
            )
        )
        result = await self.session.exec(statement)
        return result.one() > 0

    async def _get_descendants(self, path: str) -> Sequence[WorkflowFolder]:
        """Get all descendant folders of a given path."""
        # Ensure path has trailing slash
        if not path.endswith("/") and path != "/":
            path += "/"

        statement = select(WorkflowFolder).where(
            WorkflowFolder.owner_id == self.workspace_id,
            col(WorkflowFolder.path).startswith(path),
            col(WorkflowFolder.path) != path,
        )
        result = await self.session.exec(statement)
        return result.all()

    async def get_directory_items(self, path: str = "/") -> Sequence[DirectoryItem]:
        """Get all directory items (workflows and folders) in the given path.

        Args:
            path: The folder path to list items from. Use "/" for root level items.

        Returns:
            Sequence of DirectoryItems (workflows and folders) in the path
        """
        # Ensure path has trailing slash
        if not path.endswith("/") and path != "/":
            path += "/"

        # For root path, get workflows with no folder_id
        if path == "/":
            # Get root-level workflows (those with no folder)
            workflow_statement = select(Workflow).where(
                Workflow.owner_id == self.workspace_id,
                col(Workflow.folder_id).is_(None),
            )
            workflow_result = await self.session.exec(workflow_statement)
            workflows = workflow_result.all()

            # Get root-level folders
            folder_statement = select(WorkflowFolder).where(
                WorkflowFolder.owner_id == self.workspace_id,
                func.length(WorkflowFolder.path)
                - func.length(func.replace(WorkflowFolder.path, "/", ""))
                == 2,  # folders with exactly two slashes
            )
            folder_result = await self.session.exec(folder_statement)
            folders = folder_result.all()
        else:
            # Get the folder first
            folder_statement = select(WorkflowFolder).where(
                WorkflowFolder.owner_id == self.workspace_id,
                WorkflowFolder.path == path,
            )
            folder_result = await self.session.exec(folder_statement)
            folder = folder_result.one_or_none()

            if not folder:
                return []

            # Get workflows in this folder
            workflow_statement = select(Workflow).where(
                Workflow.owner_id == self.workspace_id, Workflow.folder_id == folder.id
            )
            workflow_result = await self.session.exec(workflow_statement)
            workflows = workflow_result.all()

            # Get direct child folders
            folder_statement = select(WorkflowFolder).where(
                WorkflowFolder.owner_id == self.workspace_id,
                col(WorkflowFolder.path).startswith(path),
                col(WorkflowFolder.path) != path,
                ~col(WorkflowFolder.path).like(f"{path}%/%/"),  # Exclude nested folders
            )
            folder_result = await self.session.exec(folder_statement)
            folders = folder_result.all()

        # Convert to directory items
        directory_items: list[DirectoryItem] = []

        # Add folders with item counts
        for folder in folders:
            has_children = await self._has_children(folder.path)
            has_workflows = await self._has_workflows(folder.id)
            num_items = (1 if has_children else 0) + (1 if has_workflows else 0)

            directory_items.append(
                FolderDirectoryItem(
                    type="folder",
                    num_items=num_items,
                    **folder.model_dump(),
                )
            )
        # Add workflows
        for workflow in workflows:
            directory_items.append(
                WorkflowDirectoryItem(
                    type="workflow",
                    id=WorkflowUUID.new(workflow.id).short(),
                    alias=workflow.alias,
                    created_at=workflow.created_at,
                    updated_at=workflow.updated_at,
                    version=workflow.version,
                    title=workflow.title,
                    description=workflow.description,
                    status=workflow.status,
                    icon_url=workflow.icon_url,
                    tags=[
                        TagRead.model_validate(tag, from_attributes=True)
                        for tag in workflow.tags
                    ],
                )
            )

        return directory_items
