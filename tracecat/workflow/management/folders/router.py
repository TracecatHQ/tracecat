from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError, NoResultFound

from tracecat.auth.dependencies import WorkspaceUserRole
from tracecat.db.dependencies import AsyncDBSession
from tracecat.types.exceptions import TracecatValidationError
from tracecat.workflow.management.folders.models import (
    DirectoryItem,
    WorkflowFolderCreate,
    WorkflowFolderDelete,
    WorkflowFolderMove,
    WorkflowFolderRead,
    WorkflowFolderUpdate,
)
from tracecat.workflow.management.folders.service import WorkflowFolderService

router = APIRouter(prefix="/folders", tags=["folders"])


@router.get("/directory")
async def get_directory(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    path: str = Query(default="/", description="Folder path"),
) -> list[DirectoryItem]:
    """Get directory items (workflows and folders) in the given path."""
    service = WorkflowFolderService(session, role=role)
    result = await service.get_directory_items(path)
    return list(result)


@router.get("")
async def list_folders(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    parent_path: str = Query(default="/", description="Parent folder path"),
) -> list[WorkflowFolderRead]:
    """List folders under the specified parent path.

    If parent_path is not provided, returns root-level folders.
    """
    service = WorkflowFolderService(session, role=role)
    folders = await service.list_folders(parent_path=parent_path)

    # Filter out the parent itself if present in results
    folders = [f for f in folders if f.path != parent_path]

    return [
        WorkflowFolderRead.model_validate(folder, from_attributes=True)
        for folder in folders
    ]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_folder(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    params: WorkflowFolderCreate,
) -> WorkflowFolderRead:
    """Create a new folder."""
    service = WorkflowFolderService(session, role=role)
    try:
        folder = await service.create_folder(
            name=params.name, parent_path=params.parent_path
        )
        return WorkflowFolderRead.model_validate(folder, from_attributes=True)
    except TracecatValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A folder with this name already exists at this location",
        ) from e


@router.get("/{folder_id}")
async def get_folder(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    folder_id: UUID,
) -> WorkflowFolderRead:
    """Get folder details by ID."""
    service = WorkflowFolderService(session, role=role)
    folder = await service.get_folder(folder_id)
    if not folder:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Folder not found"
        )
    return WorkflowFolderRead.model_validate(folder, from_attributes=True)


@router.patch("/{folder_id}")
async def update_folder(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    folder_id: UUID,
    params: WorkflowFolderUpdate,
) -> WorkflowFolderRead:
    """Update a folder (rename)."""
    service = WorkflowFolderService(session, role=role)
    try:
        # Skip if name is None
        if params.name is None:
            # Just return the current folder state
            folder = await service.get_folder(folder_id)
            if not folder:
                raise NoResultFound()
        else:
            folder = await service.rename_folder(folder_id, params.name)
        return WorkflowFolderRead.model_validate(folder, from_attributes=True)
    except NoResultFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Folder not found"
        ) from e
    except IntegrityError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A folder with this name already exists at this location",
        ) from e


@router.delete("/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_folder(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    folder_id: UUID,
    params: WorkflowFolderDelete,
) -> None:
    """Delete a folder.

    If recursive=true, deletes all subfolders and moves the contained workflows to root.
    If recursive=false (default), fails if the folder contains any subfolders or workflows.
    """
    service = WorkflowFolderService(session, role=role)
    try:
        await service.delete_folder(folder_id, recursive=params.recursive)
    except NoResultFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Folder not found"
        ) from e
    except TracecatValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e


@router.post("/{folder_id}/move")
async def move_folder(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    folder_id: UUID,
    params: WorkflowFolderMove,
) -> WorkflowFolderRead:
    """Move a folder to a new parent folder."""
    service = WorkflowFolderService(session, role=role)
    try:
        # If new_parent_path is provided, get the folder ID for that path
        new_parent_id = None
        if params.new_parent_path:
            if params.new_parent_path != "/":  # Root doesn't have an ID
                parent_folder = await service.get_folder_by_path(params.new_parent_path)
                if not parent_folder:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Parent folder with path {params.new_parent_path} not found",
                    )
                new_parent_id = parent_folder.id

        folder = await service.move_folder(folder_id, new_parent_id)
        return WorkflowFolderRead.model_validate(folder, from_attributes=True)
    except NoResultFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Folder not found"
        ) from e
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
