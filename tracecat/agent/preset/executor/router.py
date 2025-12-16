from fastapi import APIRouter, HTTPException, status

from tracecat.agent.preset.schemas import (
    AgentPresetCreate,
    AgentPresetRead,
    AgentPresetReadMinimal,
    AgentPresetUpdate,
)
from tracecat.agent.preset.service import AgentPresetService
from tracecat.auth.dependencies import ExecutorWorkspaceRole
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatValidationError

router = APIRouter(
    prefix="/internal/agent/presets",
    tags=["internal-agent-presets"],
    include_in_schema=False,
)


@router.get("", response_model=list[AgentPresetReadMinimal])
async def executor_list_agent_presets(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
) -> list[AgentPresetReadMinimal]:
    service = AgentPresetService(session, role=role)
    presets = await service.list_presets()
    return [AgentPresetReadMinimal.model_validate(preset) for preset in presets]


@router.post("", response_model=AgentPresetRead, status_code=status.HTTP_201_CREATED)
async def executor_create_agent_preset(
    *,
    params: AgentPresetCreate,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
) -> AgentPresetRead:
    service = AgentPresetService(session, role=role)
    try:
        preset = await service.create_preset(params)
        return AgentPresetRead.model_validate(preset)
    except TracecatValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e


@router.get("/by-slug/{slug}", response_model=AgentPresetRead)
async def executor_get_agent_preset_by_slug(
    *,
    slug: str,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
) -> AgentPresetRead:
    service = AgentPresetService(session, role=role)
    if not (preset := await service.get_preset_by_slug(slug)):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent preset with slug '{slug}' not found",
        )
    return AgentPresetRead.model_validate(preset)


@router.patch("/by-slug/{slug}", response_model=AgentPresetRead)
async def executor_update_agent_preset_by_slug(
    *,
    slug: str,
    params: AgentPresetUpdate,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
) -> AgentPresetRead:
    service = AgentPresetService(session, role=role)
    if not (preset := await service.get_preset_by_slug(slug)):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent preset with slug '{slug}' not found",
        )
    updated = await service.update_preset(preset, params)
    return AgentPresetRead.model_validate(updated)


@router.delete("/by-slug/{slug}", status_code=status.HTTP_204_NO_CONTENT)
async def executor_delete_agent_preset_by_slug(
    *,
    slug: str,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
) -> None:
    service = AgentPresetService(session, role=role)
    if not (preset := await service.get_preset_by_slug(slug)):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent preset with slug '{slug}' not found",
        )
    await service.delete_preset(preset)
