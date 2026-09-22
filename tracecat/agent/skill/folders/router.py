"""HTTP routes for skill folder management."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from tracecat import config
from tracecat.agent.skill.folders.schemas import (
    DirectoryItem,
    SkillFolderCreate,
    SkillFolderDelete,
    SkillFolderMove,
    SkillFolderRead,
    SkillFolderUpdate,
)
from tracecat.agent.skill.folders.service import (
    SkillFolderErrorCode,
    SkillFolderService,
)
from tracecat.auth.dependencies import WorkspaceActorRouteRole
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.pagination import CursorPaginatedResponse, CursorPaginationParams

router = APIRouter(prefix="/skill-folders", tags=["skill-folders"])


def _folder_http_exception(err: TracecatValidationError) -> HTTPException:
    detail = err.detail if isinstance(err.detail, dict) else {}
    raw_code = detail.get("code") if isinstance(detail, dict) else None
    try:
        code = SkillFolderErrorCode(raw_code)
    except (TypeError, ValueError):
        code = None

    if code in {SkillFolderErrorCode.NOT_FOUND, SkillFolderErrorCode.PARENT_NOT_FOUND}:
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(err))
    if code == SkillFolderErrorCode.CONFLICT:
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(err))
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err))


@router.get("/directory", response_model=list[DirectoryItem])
@require_scope("agent:read")
async def get_directory(
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    path: str = Query(default="/", description="Folder path"),
) -> list[DirectoryItem]:
    """Get directory items (skills and folders) in the given path."""
    service = SkillFolderService(session, role=role)
    try:
        return list(await service.get_directory_items(path, order_by="desc"))
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except TracecatValidationError as e:
        raise _folder_http_exception(e) from e


@router.get("", response_model=CursorPaginatedResponse[SkillFolderRead])
@require_scope("agent:read")
async def list_folders(
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    parent_path: str = Query(default="/", description="Parent folder path"),
    limit: int = Query(
        default=config.TRACECAT__LIMIT_DEFAULT,
        ge=config.TRACECAT__LIMIT_MIN,
        le=config.TRACECAT__LIMIT_CURSOR_MAX,
    ),
    cursor: str | None = Query(default=None),
    reverse: bool = Query(default=False),
) -> CursorPaginatedResponse[SkillFolderRead]:
    """List folders under the specified parent path."""
    service = SkillFolderService(session, role=role)
    try:
        page = await service.list_folders_paginated(
            parent_path=parent_path,
            params=CursorPaginationParams(
                limit=limit,
                cursor=cursor,
                reverse=reverse,
            ),
        )
    except TracecatValidationError as e:
        raise _folder_http_exception(e) from e
    return CursorPaginatedResponse(
        items=SkillFolderRead.list_adapter().validate_python(page.items),
        next_cursor=page.next_cursor,
        prev_cursor=page.prev_cursor,
        has_more=page.has_more,
        has_previous=page.has_previous,
        total_estimate=page.total_estimate,
    )


@router.post("", status_code=status.HTTP_201_CREATED)
@require_scope("agent:create")
async def create_folder(
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    params: SkillFolderCreate,
) -> SkillFolderRead:
    """Create a new skill folder."""
    service = SkillFolderService(session, role=role)
    try:
        folder = await service.create_folder(
            name=params.name, parent_path=params.parent_path
        )
        return SkillFolderRead.model_validate(folder, from_attributes=True)
    except TracecatValidationError as e:
        raise _folder_http_exception(e) from e


@router.get("/{folder_id}")
@require_scope("agent:read")
async def get_folder(
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    folder_id: UUID,
) -> SkillFolderRead:
    """Get folder details by ID."""
    service = SkillFolderService(session, role=role)
    folder = await service.get_folder(folder_id)
    if not folder:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Folder not found"
        )
    return SkillFolderRead.model_validate(folder, from_attributes=True)


@router.patch("/{folder_id}")
@require_scope("agent:update")
async def update_folder(
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    folder_id: UUID,
    params: SkillFolderUpdate,
) -> SkillFolderRead:
    """Update a folder (rename)."""
    service = SkillFolderService(session, role=role)
    if params.name is None:
        folder = await service.get_folder(folder_id)
        if not folder:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Folder not found"
            )
        return SkillFolderRead.model_validate(folder, from_attributes=True)

    try:
        folder = await service.rename_folder(folder_id, params.name)
    except TracecatValidationError as e:
        raise _folder_http_exception(e) from e
    else:
        return SkillFolderRead.model_validate(folder, from_attributes=True)


@router.delete("/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("agent:delete")
async def delete_folder(
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    folder_id: UUID,
    params: SkillFolderDelete | None = None,
) -> None:
    """Delete a skill folder."""
    service = SkillFolderService(session, role=role)
    try:
        recursive = params.recursive if params is not None else False
        await service.delete_folder(folder_id, recursive=recursive)
    except TracecatValidationError as e:
        raise _folder_http_exception(e) from e


@router.post("/{folder_id}/move")
@require_scope("agent:update")
async def move_folder(
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    folder_id: UUID,
    params: SkillFolderMove,
) -> SkillFolderRead:
    """Move a folder to a new parent folder."""
    service = SkillFolderService(session, role=role)
    new_parent_id: UUID | None = None

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
    except TracecatValidationError as e:
        raise _folder_http_exception(e) from e
    else:
        return SkillFolderRead.model_validate(folder, from_attributes=True)
