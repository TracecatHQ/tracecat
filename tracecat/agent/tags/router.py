"""HTTP routes for agent preset tag associations."""

from fastapi import APIRouter, HTTPException, status
from pydantic import UUID4

from tracecat.agent.tags.schemas import AgentPresetTagCreate, AgentTagRead
from tracecat.agent.tags.service import AgentTagsService
from tracecat.auth.dependencies import WorkspaceUserRouteRole
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatNotFoundError

router = APIRouter(prefix="/agent/presets", tags=["agent-presets"])


@router.get("/{preset_id}/tags", response_model=list[AgentTagRead])
@require_scope("agent:read")
async def list_preset_tags(
    role: WorkspaceUserRouteRole,
    session: AsyncDBSession,
    preset_id: UUID4,
) -> list[AgentTagRead]:
    """List all tags for an agent preset."""
    service = AgentTagsService(session, role=role)
    try:
        db_tags = await service.list_tags_for_preset(preset_id)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    return AgentTagRead.list_adapter().validate_python(db_tags)


@router.post("/{preset_id}/tags", status_code=status.HTTP_201_CREATED)
@require_scope("agent:update")
async def add_preset_tag(
    role: WorkspaceUserRouteRole,
    session: AsyncDBSession,
    preset_id: UUID4,
    params: AgentPresetTagCreate,
) -> None:
    """Add a tag to an agent preset."""
    service = AgentTagsService(session, role=role)
    try:
        await service.add_preset_tag(preset_id, params.tag_id)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e


@router.delete("/{preset_id}/tags/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("agent:update")
async def remove_preset_tag(
    role: WorkspaceUserRouteRole,
    session: AsyncDBSession,
    preset_id: UUID4,
    tag_id: UUID4,
) -> None:
    """Remove a tag from an agent preset."""
    service = AgentTagsService(session, role=role)
    try:
        link = await service.get_preset_tag(preset_id, tag_id)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    await service.remove_preset_tag(link)
