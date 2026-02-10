import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, status

from tracecat.agent.preset.schemas import (
    AgentPresetCreate,
    AgentPresetRead,
    AgentPresetReadMinimal,
    AgentPresetUpdate,
)
from tracecat.agent.preset.service import AgentPresetService
from tracecat.auth.credentials import RoleACL
from tracecat.auth.types import Role
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatValidationError

router = APIRouter(prefix="/agent/presets", tags=["agent-presets"])

WorkspaceEditorRole = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="yes",
    ),
]


@router.get("", response_model=list[AgentPresetReadMinimal])
@require_scope("agent:read")
async def list_agent_presets(
    *,
    role: WorkspaceEditorRole,
    session: AsyncDBSession,
) -> list[AgentPresetReadMinimal]:
    """List all agent presets for the current workspace."""
    service = AgentPresetService(session, role=role)
    presets = await service.list_presets()
    return [AgentPresetReadMinimal.model_validate(preset) for preset in presets]


@router.post(
    "",
    response_model=AgentPresetRead,
    status_code=status.HTTP_201_CREATED,
)
@require_scope("agent:update")
async def create_agent_preset(
    *,
    params: AgentPresetCreate,
    role: WorkspaceEditorRole,
    session: AsyncDBSession,
) -> AgentPresetRead:
    """Create a new agent preset."""
    service = AgentPresetService(session, role=role)
    try:
        preset = await service.create_preset(params)
        return AgentPresetRead.model_validate(preset)
    except TracecatValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e


@router.get("/{preset_id}", response_model=AgentPresetRead)
@require_scope("agent:read")
async def get_agent_preset(
    *,
    preset_id: uuid.UUID,
    role: WorkspaceEditorRole,
    session: AsyncDBSession,
) -> AgentPresetRead:
    """Retrieve an agent preset by ID."""
    service = AgentPresetService(session, role=role)
    if not (preset := await service.get_preset(preset_id)):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent preset with ID '{preset_id}' not found",
        )
    return AgentPresetRead.model_validate(preset)


@router.get("/by-slug/{slug}", response_model=AgentPresetRead)
@require_scope("agent:read")
async def get_agent_preset_by_slug(
    *,
    slug: str,
    role: WorkspaceEditorRole,
    session: AsyncDBSession,
) -> AgentPresetRead:
    """Retrieve an agent preset by slug."""
    service = AgentPresetService(session, role=role)
    if not (preset := await service.get_preset_by_slug(slug)):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent preset with slug '{slug}' not found",
        )
    return AgentPresetRead.model_validate(preset)


@router.patch("/{preset_id}", response_model=AgentPresetRead)
@require_scope("agent:update")
async def update_agent_preset(
    *,
    preset_id: uuid.UUID,
    params: AgentPresetUpdate,
    role: WorkspaceEditorRole,
    session: AsyncDBSession,
) -> AgentPresetRead:
    """Update an existing agent preset."""
    service = AgentPresetService(session, role=role)
    if not (preset := await service.get_preset(preset_id)):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent preset {preset_id} not found",
        )
    preset = await service.update_preset(preset, params)
    return AgentPresetRead.model_validate(preset)


@router.delete("/{preset_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("agent:delete")
async def delete_agent_preset(
    *,
    preset_id: uuid.UUID,
    role: WorkspaceEditorRole,
    session: AsyncDBSession,
) -> None:
    """Delete an agent preset."""
    service = AgentPresetService(session, role=role)
    if not (preset := await service.get_preset(preset_id)):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent preset {preset_id} not found",
        )
    await service.delete_preset(preset)
