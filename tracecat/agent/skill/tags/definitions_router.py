"""HTTP routes for skill tag definition CRUD."""

from fastapi import APIRouter, HTTPException, Query, status

from tracecat import config
from tracecat.agent.skill.tags.schemas import SkillTagRead
from tracecat.agent.skill.tags.service import SkillTagsService
from tracecat.auth.dependencies import WorkspaceActorRouteRole
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import (
    TracecatConflictError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.identifiers import SkillTagID
from tracecat.pagination import CursorPaginatedResponse, CursorPaginationParams
from tracecat.tags.schemas import TagCreate, TagUpdate

router = APIRouter(prefix="/skill-tags", tags=["skill-tags"])


@router.get("", response_model=CursorPaginatedResponse[SkillTagRead])
@require_scope("agent:read")
async def list_skill_tags(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    limit: int = Query(
        default=config.TRACECAT__LIMIT_DEFAULT,
        ge=config.TRACECAT__LIMIT_MIN,
        le=config.TRACECAT__LIMIT_CURSOR_MAX,
    ),
    cursor: str | None = Query(default=None),
    reverse: bool = Query(default=False),
) -> CursorPaginatedResponse[SkillTagRead]:
    """List all skill tags in the workspace."""
    service = SkillTagsService(session=session, role=role)
    try:
        page = await service.list_tags_paginated(
            CursorPaginationParams(limit=limit, cursor=cursor, reverse=reverse)
        )
    except TracecatValidationError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(err),
        ) from err
    return CursorPaginatedResponse(
        items=[
            SkillTagRead.model_validate(tag, from_attributes=True) for tag in page.items
        ],
        next_cursor=page.next_cursor,
        prev_cursor=page.prev_cursor,
        has_more=page.has_more,
        has_previous=page.has_previous,
        total_estimate=page.total_estimate,
    )


@router.get("/{tag_id}", response_model=SkillTagRead)
@require_scope("agent:read")
async def get_skill_tag(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    tag_id: SkillTagID,
) -> SkillTagRead:
    """Get a skill tag by ID."""
    service = SkillTagsService(session=session, role=role)
    try:
        tag = await service.get_tag(tag_id)
    except TracecatNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(err),
        ) from err
    return SkillTagRead.model_validate(tag, from_attributes=True)


@router.post("", response_model=SkillTagRead, status_code=status.HTTP_201_CREATED)
@require_scope("agent:create")
async def create_skill_tag(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    params: TagCreate,
) -> SkillTagRead:
    """Create a new skill tag definition."""
    service = SkillTagsService(session=session, role=role)
    try:
        tag = await service.create_tag(params)
    except TracecatConflictError as err:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(err),
        ) from err
    return SkillTagRead.model_validate(tag, from_attributes=True)


@router.patch("/{tag_id}", response_model=SkillTagRead)
@require_scope("agent:update")
async def update_skill_tag(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    tag_id: SkillTagID,
    params: TagUpdate,
) -> SkillTagRead:
    """Update a skill tag definition."""
    service = SkillTagsService(session=session, role=role)
    try:
        tag = await service.get_tag(tag_id)
    except TracecatNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(err),
        ) from err
    try:
        updated = await service.update_tag(tag, params)
    except TracecatConflictError as err:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(err),
        ) from err
    return SkillTagRead.model_validate(updated, from_attributes=True)


@router.delete("/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("agent:delete")
async def delete_skill_tag(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    tag_id: SkillTagID,
) -> None:
    """Delete a skill tag definition."""
    service = SkillTagsService(session=session, role=role)
    try:
        await service.delete_tag_by_id(tag_id)
    except TracecatNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(err),
        ) from err
