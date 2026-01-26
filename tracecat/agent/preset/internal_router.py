"""Internal router for agent preset operations (SDK/UDF use)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from tracecat.agent.preset.schemas import (
    AgentPresetRead,
    AgentPresetReadMinimal,
    AgentPresetUpdate,
)
from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.types import OutputType
from tracecat.auth.dependencies import ExecutorWorkspaceRole
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatValidationError

router = APIRouter(
    prefix="/internal/agent/presets",
    tags=["internal-agent-presets"],
    include_in_schema=False,
)


class PresetCreateRequest(BaseModel):
    """Request body for creating an agent preset."""

    name: str = Field(..., min_length=1, max_length=120)
    model_name: str = Field(..., min_length=1, max_length=120)
    model_provider: str = Field(..., min_length=1, max_length=120)
    slug: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=1000)
    instructions: str | None = Field(default=None)
    base_url: str | None = Field(default=None, max_length=500)
    output_type: OutputType | None = Field(default=None)
    actions: list[str] | None = Field(default=None)


class PresetUpdateRequest(BaseModel):
    """Request body for updating an agent preset."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    slug: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=1000)
    instructions: str | None = Field(default=None)
    model_name: str | None = Field(default=None, min_length=1, max_length=120)
    model_provider: str | None = Field(default=None, min_length=1, max_length=120)
    base_url: str | None = Field(default=None, max_length=500)
    output_type: OutputType | None = Field(default=None)
    actions: list[str] | None = Field(default=None)


@router.get("", response_model=list[AgentPresetReadMinimal])
async def list_presets(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
) -> list[AgentPresetReadMinimal]:
    """List all agent presets for the workspace."""
    service = AgentPresetService(session, role=role)
    presets = await service.list_presets()
    return [AgentPresetReadMinimal.model_validate(preset) for preset in presets]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_preset(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    params: PresetCreateRequest,
) -> dict[str, Any]:
    """Create a new agent preset."""
    from tracecat.agent.preset.schemas import AgentPresetCreate

    service = AgentPresetService(session, role=role)
    try:
        preset = await service.create_preset(
            AgentPresetCreate(**params.model_dump(exclude_unset=True))
        )
        return AgentPresetRead.model_validate(preset).model_dump(mode="json")
    except TracecatValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.get("/by-slug/{slug}")
async def get_preset_by_slug(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    slug: str,
) -> dict[str, Any]:
    """Get an agent preset by slug."""
    service = AgentPresetService(session, role=role)
    preset = await service.get_preset_by_slug(slug)
    if not preset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent preset with slug '{slug}' not found",
        )
    return AgentPresetRead.model_validate(preset).model_dump(mode="json")


@router.patch("/by-slug/{slug}")
async def update_preset_by_slug(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    slug: str,
    params: PresetUpdateRequest,
) -> dict[str, Any]:
    """Update an agent preset by slug."""
    service = AgentPresetService(session, role=role)
    preset = await service.get_preset_by_slug(slug)
    if not preset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent preset with slug '{slug}' not found",
        )
    updated_preset = await service.update_preset(
        preset, AgentPresetUpdate(**params.model_dump(exclude_unset=True))
    )
    return AgentPresetRead.model_validate(updated_preset).model_dump(mode="json")


@router.delete("/by-slug/{slug}", status_code=status.HTTP_204_NO_CONTENT)
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
