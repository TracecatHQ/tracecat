import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, status

from tracecat.agent.presets.schemas import (
    AgentPresetCreate,
    AgentPresetRead,
    AgentPresetUpdate,
)
from tracecat.agent.service import AgentManagementService
from tracecat.auth.credentials import RoleACL
from tracecat.auth.types import Role
from tracecat.authz.enums import WorkspaceRole
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError

router = APIRouter(prefix="/agent/presets", tags=["agent-presets"])

WorkspaceEditorRole = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="yes",
        require_workspace_roles=[WorkspaceRole.EDITOR, WorkspaceRole.ADMIN],
    ),
]


@router.get("", response_model=list[AgentPresetRead])
async def list_agent_presets(
    *,
    role: WorkspaceEditorRole,
    session: AsyncDBSession,
) -> list[AgentPresetRead]:
    """List all agent presets for the current workspace."""

    service = AgentManagementService(session, role=role)
    presets = await service.list_agent_presets()
    return [AgentPresetRead.model_validate(preset) for preset in presets]


@router.post(
    "",
    response_model=AgentPresetRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_agent_preset(
    *,
    params: AgentPresetCreate,
    role: WorkspaceEditorRole,
    session: AsyncDBSession,
) -> AgentPresetRead:
    """Create a new agent preset."""

    service = AgentManagementService(session, role=role)
    try:
        preset = await service.create_agent_preset(params)
        return AgentPresetRead.model_validate(preset)
    except TracecatValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e


@router.get("/{preset_id}", response_model=AgentPresetRead)
async def get_agent_preset(
    *,
    preset_id: uuid.UUID,
    role: WorkspaceEditorRole,
    session: AsyncDBSession,
) -> AgentPresetRead:
    """Retrieve an agent preset by ID."""

    service = AgentManagementService(session, role=role)
    try:
        preset = await service.get_agent_preset(preset_id)
        return AgentPresetRead.model_validate(preset)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent preset {preset_id} not found",
        ) from e


@router.get("/by-slug/{slug}", response_model=AgentPresetRead)
async def get_agent_preset_by_slug(
    *,
    slug: str,
    role: WorkspaceEditorRole,
    session: AsyncDBSession,
) -> AgentPresetRead:
    """Retrieve an agent preset by slug."""

    service = AgentManagementService(session, role=role)
    try:
        preset = await service.get_agent_preset_by_slug(slug)
        return AgentPresetRead.model_validate(preset)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent preset '{slug}' not found",
        ) from e


@router.patch("/{preset_id}", response_model=AgentPresetRead)
async def update_agent_preset(
    *,
    preset_id: uuid.UUID,
    params: AgentPresetUpdate,
    role: WorkspaceEditorRole,
    session: AsyncDBSession,
) -> AgentPresetRead:
    """Update an existing agent preset."""

    service = AgentManagementService(session, role=role)
    try:
        preset = await service.update_agent_preset(preset_id, params)
        return AgentPresetRead.model_validate(preset)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent preset {preset_id} not found",
        ) from e
    except TracecatValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e


@router.delete("/{preset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent_preset(
    *,
    preset_id: uuid.UUID,
    role: WorkspaceEditorRole,
    session: AsyncDBSession,
) -> None:
    """Delete an agent preset."""

    service = AgentManagementService(session, role=role)
    try:
        await service.delete_agent_preset(preset_id)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent preset {preset_id} not found",
        ) from e
