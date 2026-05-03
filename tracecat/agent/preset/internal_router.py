"""Internal router for agent preset operations (SDK/UDF use)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, StringConstraints

from tracecat.agent.preset.schemas import (
    AgentPresetCreate,
    AgentPresetRead,
    AgentPresetReadMinimal,
    AgentPresetSkillBindingBase,
    AgentPresetUpdate,
    build_agent_preset_read_minimal,
)
from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.types import OutputType
from tracecat.auth.dependencies import ExecutorWorkspaceRole
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatValidationError

router = APIRouter(
    prefix="/internal/agent/presets",
    tags=["internal-agent-presets"],
    include_in_schema=False,
)

PresetName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=120),
]
PresetSlug = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=160),
]
PresetModelField = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=120),
]


class PresetCreateRequest(BaseModel):
    """Request body for creating an agent preset."""

    name: PresetName
    model_name: PresetModelField
    model_provider: PresetModelField
    slug: PresetSlug | None = None
    description: str | None = Field(default=None, max_length=1000)
    instructions: str | None = Field(default=None)
    base_url: str | None = Field(default=None, max_length=500)
    output_type: OutputType | None = Field(default=None)
    actions: list[str] | None = Field(default=None)
    enable_thinking: bool = Field(default=True)
    enable_internet_access: bool = Field(default=False)
    skills: list[AgentPresetSkillBindingBase] | None = Field(default=None)


class PresetUpdateRequest(BaseModel):
    """Request body for updating an agent preset."""

    name: PresetName | None = None
    slug: PresetSlug | None = None
    description: str | None = Field(default=None, max_length=1000)
    instructions: str | None = Field(default=None)
    model_name: PresetModelField | None = None
    model_provider: PresetModelField | None = None
    base_url: str | None = Field(default=None, max_length=500)
    output_type: OutputType | None = Field(default=None)
    actions: list[str] | None = Field(default=None)
    enable_thinking: bool | None = Field(default=None)
    enable_internet_access: bool | None = Field(default=None)
    skills: list[AgentPresetSkillBindingBase] | None = Field(default=None)


@router.get("", response_model=list[AgentPresetReadMinimal])
@require_scope("agent:read")
async def list_presets(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
) -> list[AgentPresetReadMinimal]:
    """List all agent presets for the workspace."""
    service = AgentPresetService(session, role=role)
    presets = await service.list_presets()
    return [build_agent_preset_read_minimal(preset) for preset in presets]


@router.post("", status_code=status.HTTP_201_CREATED)
@require_scope("agent:create")
async def create_preset(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    params: PresetCreateRequest,
) -> AgentPresetRead:
    """Create a new agent preset."""
    service = AgentPresetService(session, role=role)
    try:
        preset = await service.create_preset(
            AgentPresetCreate(**params.model_dump(exclude_unset=True))
        )
        return await service.build_preset_read(preset)
    except TracecatValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.get("/by-slug/{slug}")
@require_scope("agent:read")
async def get_preset_by_slug(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    slug: str,
) -> AgentPresetRead:
    """Get an agent preset by slug."""
    service = AgentPresetService(session, role=role)
    preset = await service.get_preset_by_slug(slug)
    if not preset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent preset with slug '{slug}' not found",
        )
    return await service.build_preset_read(preset)


@router.patch("/by-slug/{slug}")
@require_scope("agent:update")
async def update_preset_by_slug(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    slug: str,
    params: PresetUpdateRequest,
) -> AgentPresetRead:
    """Update an agent preset by slug."""
    service = AgentPresetService(session, role=role)
    preset = await service.get_preset_by_slug(slug)
    if not preset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent preset with slug '{slug}' not found",
        )
    try:
        updated_preset = await service.update_preset(
            preset, AgentPresetUpdate(**params.model_dump(exclude_unset=True))
        )
        return await service.build_preset_read(updated_preset)
    except TracecatValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.delete("/by-slug/{slug}", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("agent:delete")
async def delete_preset_by_slug(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    slug: str,
) -> None:
    """Delete an agent preset by slug."""
    service = AgentPresetService(session, role=role)
    preset = await service.get_preset_by_slug(slug)
    if not preset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent preset with slug '{slug}' not found",
        )
    await service.delete_preset(preset)
