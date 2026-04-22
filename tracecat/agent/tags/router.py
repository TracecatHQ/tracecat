"""HTTP routes for agent preset tag associations."""

from fastapi import APIRouter, HTTPException, status
from pydantic import UUID4
from sqlalchemy.exc import NoResultFound

from tracecat.agent.tags.schemas import AgentPresetTagCreate, AgentTagRead
from tracecat.agent.tags.service import AgentTagsService
from tracecat.auth.dependencies import WorkspaceUserRole
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession

router = APIRouter(prefix="/agent/presets", tags=["agent-presets"])


@router.get("/{preset_id}/tags", response_model=list[AgentTagRead])
@require_scope("agent:read")
async def list_preset_tags(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    preset_id: UUID4,
) -> list[AgentTagRead]:
    """List all tags for an agent preset."""
    service = AgentTagsService(session, role=role)
    db_tags = await service.list_tags_for_preset(preset_id)
    return [AgentTagRead.model_validate(tag, from_attributes=True) for tag in db_tags]


@router.post("/{preset_id}/tags", status_code=status.HTTP_201_CREATED)
@require_scope("agent:update")
async def add_preset_tag(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    preset_id: UUID4,
    params: AgentPresetTagCreate,
) -> None:
    """Add a tag to an agent preset."""
    service = AgentTagsService(session, role=role)
    try:
        await service.add_preset_tag(preset_id, params.tag_id)
    except NoResultFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent preset or tag not found",
        ) from e


@router.delete("/{preset_id}/tags/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("agent:update")
async def remove_preset_tag(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    preset_id: UUID4,
    tag_id: UUID4,
) -> None:
    """Remove a tag from an agent preset."""
    service = AgentTagsService(session, role=role)
    try:
        link = await service.get_preset_tag(preset_id, tag_id)
    except NoResultFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tag not found",
        ) from e
    await service.remove_preset_tag(link)
