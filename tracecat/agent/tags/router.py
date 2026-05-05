"""HTTP routes for agent tag definitions and preset-tag links."""

from fastapi import APIRouter, HTTPException, status

from tracecat.agent.tags.schemas import (
    AgentPresetTagCreate,
    AgentTagCreate,
    AgentTagRead,
    AgentTagUpdate,
)
from tracecat.agent.tags.service import AgentTagsService
from tracecat.auth.dependencies import WorkspaceUserRouteRole
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatConflictError, TracecatNotFoundError
from tracecat.identifiers import AgentTagID

router = APIRouter(prefix="/agent/tags", tags=["agent-tags"])
preset_tags_router = APIRouter(prefix="/agent/presets", tags=["agent-presets"])


# --- Tag definition CRUD ---


@router.get("")
@require_scope("agent:read")
async def list_tags(
    role: WorkspaceUserRouteRole,
    session: AsyncDBSession,
) -> list[AgentTagRead]:
    """List all agent tags in the workspace."""
    service = AgentTagsService(session, role=role)
    tags = await service.list_tags()
    return [AgentTagRead.model_validate(tag, from_attributes=True) for tag in tags]


@router.get("/{tag_id}")
@require_scope("agent:read")
async def get_tag(
    role: WorkspaceUserRouteRole,
    session: AsyncDBSession,
    tag_id: AgentTagID,
) -> AgentTagRead:
    """Get a single agent tag by ID."""
    service = AgentTagsService(session, role=role)
    try:
        tag = await service.get_tag(tag_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return AgentTagRead.model_validate(tag, from_attributes=True)


@router.post("", status_code=status.HTTP_201_CREATED)
@require_scope("agent:create")
async def create_tag(
    role: WorkspaceUserRouteRole,
    session: AsyncDBSession,
    params: AgentTagCreate,
) -> AgentTagRead:
    """Create a new agent tag definition."""
    service = AgentTagsService(session, role=role)
    try:
        tag = await service.create_tag(params)
    except TracecatConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    return AgentTagRead.model_validate(tag, from_attributes=True)


@router.patch("/{tag_id}")
@require_scope("agent:update")
async def update_tag(
    role: WorkspaceUserRouteRole,
    session: AsyncDBSession,
    tag_id: AgentTagID,
    params: AgentTagUpdate,
) -> AgentTagRead:
    """Update an agent tag's name or color."""
    service = AgentTagsService(session, role=role)
    try:
        tag = await service.get_tag(tag_id)
        tag = await service.update_tag(tag, params)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except TracecatConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    return AgentTagRead.model_validate(tag, from_attributes=True)


@router.delete("/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("agent:delete")
async def delete_tag(
    role: WorkspaceUserRouteRole,
    session: AsyncDBSession,
    tag_id: AgentTagID,
) -> None:
    """Delete an agent tag definition."""
    service = AgentTagsService(session, role=role)
    try:
        tag = await service.get_tag(tag_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    await service.delete_tag(tag)


# --- Preset-tag link operations (mounted under /agent/presets) ---


@preset_tags_router.get("/{preset_id}/tags", response_model=list[AgentTagRead])
@require_scope("agent:read")
async def list_preset_tags(
    role: WorkspaceUserRouteRole,
    session: AsyncDBSession,
    preset_id: AgentTagID,
) -> list[AgentTagRead]:
    """List tags attached to a preset."""
    service = AgentTagsService(session, role=role)
    tags = await service.list_tags_for_preset(preset_id)
    return [AgentTagRead.model_validate(tag, from_attributes=True) for tag in tags]


@preset_tags_router.post("/{preset_id}/tags", status_code=status.HTTP_201_CREATED)
@require_scope("agent:update")
async def add_preset_tag(
    role: WorkspaceUserRouteRole,
    session: AsyncDBSession,
    preset_id: AgentTagID,
    params: AgentPresetTagCreate,
) -> None:
    """Attach a tag to a preset (idempotent)."""
    service = AgentTagsService(session, role=role)
    try:
        await service.add_preset_tag(preset_id, params.tag_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@preset_tags_router.delete(
    "/{preset_id}/tags/{tag_id}", status_code=status.HTTP_204_NO_CONTENT
)
@require_scope("agent:update")
async def remove_preset_tag(
    role: WorkspaceUserRouteRole,
    session: AsyncDBSession,
    preset_id: AgentTagID,
    tag_id: AgentTagID,
) -> None:
    """Detach a tag from a preset."""
    service = AgentTagsService(session, role=role)
    try:
        link = await service.get_preset_tag_link(preset_id, tag_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    await service.remove_preset_tag(link)
