"""HTTP routes for agent folder management."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError, NoResultFound

from tracecat.agent.folders.schemas import (
    AgentFolderCreate,
    AgentFolderDelete,
    AgentFolderMove,
    AgentFolderRead,
    AgentFolderUpdate,
    DirectoryItem,
)
from tracecat.agent.folders.service import AgentFolderService
from tracecat.auth.dependencies import WorkspaceUserRole
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError

router = APIRouter(prefix="/agent-folders", tags=["agent-folders"])


@router.get("/directory")
@require_scope("agent:read")
async def get_directory(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    path: str = Query(default="/", description="Folder path"),
) -> list[DirectoryItem]:
    """Get directory items (presets and folders) in the given path."""
    service = AgentFolderService(session, role=role)
    try:
        result = await service.get_directory_items(path, order_by="desc")
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return list(result)


@router.get("")
@require_scope("agent:read")
async def list_folders(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    parent_path: str = Query(default="/", description="Parent folder path"),
) -> list[AgentFolderRead]:
    """List folders under the specified parent path."""
    service = AgentFolderService(session, role=role)
    folders = await service.list_folders(parent_path=parent_path)
    folders = [f for f in folders if f.path != parent_path]
    return [
        AgentFolderRead.model_validate(folder, from_attributes=True)
        for folder in folders
    ]


@router.post("", status_code=status.HTTP_201_CREATED)
@require_scope("agent:create")
async def create_folder(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    params: AgentFolderCreate,
) -> AgentFolderRead:
    """Create a new agent folder."""
    service = AgentFolderService(session, role=role)
    try:
        folder = await service.create_folder(
            name=params.name, parent_path=params.parent_path
        )
        return AgentFolderRead.model_validate(folder, from_attributes=True)
    except TracecatValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A folder with this name already exists at this location",
        ) from e


@router.get("/{folder_id}")
@require_scope("agent:read")
async def get_folder(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    folder_id: UUID,
) -> AgentFolderRead:
    """Get folder details by ID."""
    service = AgentFolderService(session, role=role)
    folder = await service.get_folder(folder_id)
    if not folder:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Folder not found"
        )
    return AgentFolderRead.model_validate(folder, from_attributes=True)


@router.patch("/{folder_id}")
@require_scope("agent:update")
async def update_folder(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    folder_id: UUID,
    params: AgentFolderUpdate,
) -> AgentFolderRead:
    """Update a folder (rename)."""
    service = AgentFolderService(session, role=role)
    try:
        if params.name is None:
            folder = await service.get_folder(folder_id)
            if not folder:
                raise NoResultFound()
        else:
            folder = await service.rename_folder(folder_id, params.name)
        return AgentFolderRead.model_validate(folder, from_attributes=True)
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
@require_scope("agent:delete")
async def delete_folder(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    folder_id: UUID,
    params: AgentFolderDelete,
) -> None:
    """Delete an agent folder."""
    service = AgentFolderService(session, role=role)
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
@require_scope("agent:update")
async def move_folder(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    folder_id: UUID,
    params: AgentFolderMove,
) -> AgentFolderRead:
    """Move a folder to a new parent folder."""
    service = AgentFolderService(session, role=role)
    try:
        new_parent_id = None
        if params.new_parent_path:
            if params.new_parent_path != "/":
                parent_folder = await service.get_folder_by_path(params.new_parent_path)
                if not parent_folder:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Parent folder with path {params.new_parent_path} not found",
                    )
                new_parent_id = parent_folder.id
        folder = await service.move_folder(folder_id, new_parent_id)
        return AgentFolderRead.model_validate(folder, from_attributes=True)
    except NoResultFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Folder not found"
        ) from e
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
