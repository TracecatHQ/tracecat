"""HTTP routes for agent preset folders."""

from fastapi import APIRouter, HTTPException, Query, status

from tracecat.agent.folders.schemas import (
    AgentFolderCreate,
    AgentFolderDelete,
    AgentFolderMove,
    AgentFolderRead,
    AgentFolderUpdate,
    DirectoryItem,
)
from tracecat.agent.folders.service import AgentFolderService
from tracecat.auth.dependencies import WorkspaceUserRouteRole
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import (
    TracecatConflictError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.identifiers import AgentFolderID

router = APIRouter(prefix="/agent/folders", tags=["agent-folders"])


@router.get("/directory")
@require_scope("agent:read")
async def get_directory(
    role: WorkspaceUserRouteRole,
    session: AsyncDBSession,
    path: str = Query(default="/", description="Folder path"),
) -> list[DirectoryItem]:
    """List the directory items (folders and presets) at the given path."""
    service = AgentFolderService(session, role=role)
    try:
        result = await service.get_directory_items(path, order_by="desc")
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return list(result)


@router.get("")
@require_scope("agent:read")
async def list_folders(
    role: WorkspaceUserRouteRole,
    session: AsyncDBSession,
    parent_path: str = Query(default="/", description="Parent folder path"),
) -> list[AgentFolderRead]:
    """List folders in the subtree under ``parent_path``."""
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
    role: WorkspaceUserRouteRole,
    session: AsyncDBSession,
    params: AgentFolderCreate,
) -> AgentFolderRead:
    """Create a new agent folder."""
    service = AgentFolderService(session, role=role)
    try:
        folder = await service.create_folder(
            name=params.name, parent_path=params.parent_path
        )
    except TracecatConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    except TracecatValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
    return AgentFolderRead.model_validate(folder, from_attributes=True)


@router.get("/{folder_id}")
@require_scope("agent:read")
async def get_folder(
    role: WorkspaceUserRouteRole,
    session: AsyncDBSession,
    folder_id: AgentFolderID,
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
    role: WorkspaceUserRouteRole,
    session: AsyncDBSession,
    folder_id: AgentFolderID,
    params: AgentFolderUpdate,
) -> AgentFolderRead:
    """Update a folder (rename)."""
    service = AgentFolderService(session, role=role)
    if params.name is None:
        folder = await service.get_folder(folder_id)
        if not folder:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Folder not found"
            )
        return AgentFolderRead.model_validate(folder, from_attributes=True)
    try:
        folder = await service.rename_folder(folder_id, params.name)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except TracecatConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    except TracecatValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
    return AgentFolderRead.model_validate(folder, from_attributes=True)


@router.delete("/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("agent:delete")
async def delete_folder(
    role: WorkspaceUserRouteRole,
    session: AsyncDBSession,
    folder_id: AgentFolderID,
    params: AgentFolderDelete,
) -> None:
    """Delete a folder.

    With ``recursive=True`` descendant folders are deleted and their presets
    are moved back to root. With ``recursive=False`` (default) the folder
    must be empty.
    """
    service = AgentFolderService(session, role=role)
    try:
        await service.delete_folder(folder_id, recursive=params.recursive)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except TracecatValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e


@router.post("/{folder_id}/move")
@require_scope("agent:update")
async def move_folder(
    role: WorkspaceUserRouteRole,
    session: AsyncDBSession,
    folder_id: AgentFolderID,
    params: AgentFolderMove,
) -> AgentFolderRead:
    """Move a folder to a new parent."""
    service = AgentFolderService(session, role=role)
    new_parent_id: AgentFolderID | None = None
    if params.new_parent_path and params.new_parent_path != "/":
        parent_folder = await service.get_folder_by_path(params.new_parent_path)
        if not parent_folder:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Parent folder with path {params.new_parent_path} not found",
            )
        new_parent_id = parent_folder.id

    try:
        folder = await service.move_folder(folder_id, new_parent_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except TracecatConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    except TracecatValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
    return AgentFolderRead.model_validate(folder, from_attributes=True)
