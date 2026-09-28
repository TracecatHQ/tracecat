"""HTTP routes for skill tag associations."""

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import UUID4

from tracecat import config
from tracecat.agent.skill.tags.schemas import SkillTagCreate, SkillTagRead
from tracecat.agent.skill.tags.service import SkillTagsService
from tracecat.auth.dependencies import WorkspaceActorRouteRole
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.pagination import CursorPaginatedResponse, CursorPaginationParams

router = APIRouter(prefix="/agent/skills", tags=["agent-skills"])


@router.get("/{skill_id}/tags", response_model=CursorPaginatedResponse[SkillTagRead])
@require_scope("agent:read")
async def list_skill_tags(
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    skill_id: UUID4,
    limit: int = Query(
        default=config.TRACECAT__LIMIT_DEFAULT,
        ge=config.TRACECAT__LIMIT_MIN,
        le=config.TRACECAT__LIMIT_CURSOR_MAX,
    ),
    cursor: str | None = Query(default=None),
    reverse: bool = Query(default=False),
) -> CursorPaginatedResponse[SkillTagRead]:
    """List all tags for a skill."""
    service = SkillTagsService(session, role=role)
    try:
        page = await service.list_tags_for_skill_paginated(
            skill_id,
            CursorPaginationParams(
                limit=limit,
                cursor=cursor,
                reverse=reverse,
            ),
        )
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    except TracecatValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    return CursorPaginatedResponse(
        items=SkillTagRead.list_adapter().validate_python(page.items),
        next_cursor=page.next_cursor,
        prev_cursor=page.prev_cursor,
        has_more=page.has_more,
        has_previous=page.has_previous,
        total_estimate=page.total_estimate,
    )


@router.post("/{skill_id}/tags", status_code=status.HTTP_201_CREATED)
@require_scope("agent:update")
async def add_skill_tag(
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    skill_id: UUID4,
    params: SkillTagCreate,
) -> None:
    """Add a tag to a skill."""
    service = SkillTagsService(session, role=role)
    try:
        await service.add_skill_tag(skill_id, params.tag_id)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e


@router.delete("/{skill_id}/tags/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("agent:update")
async def remove_skill_tag(
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    skill_id: UUID4,
    tag_id: UUID4,
) -> None:
    """Remove a tag from a skill."""
    service = SkillTagsService(session, role=role)
    try:
        link = await service.get_skill_tag(skill_id, tag_id)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    await service.remove_skill_tag(link)
