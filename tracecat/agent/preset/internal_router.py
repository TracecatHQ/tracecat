"""Internal router for agent preset operations (SDK/UDF use)."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Annotated, Any, cast

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, StringConstraints

from tracecat.agent.preset.schemas import (
    AgentPresetCreate,
    AgentPresetRead,
    AgentPresetReadMinimal,
    AgentPresetSkillBindingBase,
    AgentPresetUpdate,
    build_subagent_eligibility,
)
from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.service import AgentManagementService
from tracecat.agent.subagents import AgentSubagentsConfig, has_manual_tool_approvals
from tracecat.agent.types import OutputType
from tracecat.auth.dependencies import ExecutorWorkspaceRole
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError

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
    model_name: PresetModelField | None = Field(
        default=None,
        description=(
            "Deprecated legacy model name field retained for backward "
            "compatibility. Prefer catalog_id, which is the canonical model selector."
        ),
    )
    model_provider: PresetModelField | None = Field(
        default=None,
        description=(
            "Deprecated legacy model provider field retained for backward "
            "compatibility. Prefer catalog_id, which is the canonical model selector."
        ),
    )
    catalog_id: uuid.UUID | None = Field(
        default=None,
        description="Canonical model catalog row ID backing this preset.",
    )
    slug: PresetSlug | None = None
    description: str | None = Field(default=None, max_length=1000)
    instructions: str | None = Field(default=None)
    base_url: str | None = Field(default=None, max_length=500)
    output_type: OutputType | None = Field(default=None)
    actions: list[str] | None = Field(default=None)
    namespaces: list[str] | None = Field(default=None)
    tool_approvals: dict[str, bool] | None = Field(default=None)
    mcp_integrations: list[str] | None = Field(default=None)
    agents: AgentSubagentsConfig | None = Field(default=None)
    retries: int | None = Field(default=None, ge=0)
    enable_thinking: bool = Field(default=True)
    enable_internet_access: bool = Field(default=False)
    skills: list[AgentPresetSkillBindingBase] | None = Field(default=None)


class PresetUpdateRequest(BaseModel):
    """Request body for updating an agent preset."""

    name: PresetName | None = None
    slug: PresetSlug | None = None
    description: str | None = Field(default=None, max_length=1000)
    instructions: str | None = Field(default=None)
    model_name: PresetModelField | None = Field(
        default=None,
        description=(
            "Deprecated legacy model name field retained for backward "
            "compatibility. Prefer catalog_id, which is the canonical model selector."
        ),
    )
    model_provider: PresetModelField | None = Field(
        default=None,
        description=(
            "Deprecated legacy model provider field retained for backward "
            "compatibility. Prefer catalog_id, which is the canonical model selector."
        ),
    )
    catalog_id: uuid.UUID | None = Field(
        default=None,
        description="Canonical model catalog row ID backing this preset.",
    )
    base_url: str | None = Field(default=None, max_length=500)
    output_type: OutputType | None = Field(default=None)
    actions: list[str] | None = Field(default=None)
    namespaces: list[str] | None = Field(default=None)
    tool_approvals: dict[str, bool] | None = Field(default=None)
    mcp_integrations: list[str] | None = Field(default=None)
    agents: AgentSubagentsConfig | None = Field(default=None)
    retries: int | None = Field(default=None, ge=0)
    enable_thinking: bool | None = Field(default=None)
    enable_internet_access: bool | None = Field(default=None)
    skills: list[AgentPresetSkillBindingBase] | None = Field(default=None)


async def _create_payload_with_default_model(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    params: PresetCreateRequest,
) -> dict[str, Any]:
    """Fill omitted legacy model fields from the canonical default catalog model."""
    payload = params.model_dump(exclude_unset=True)
    for defaulted_field in ("agents", "retries", "catalog_id"):
        if payload.get(defaulted_field) is None:
            payload.pop(defaulted_field, None)
    has_model_name = bool(payload.get("model_name"))
    has_model_provider = bool(payload.get("model_provider"))
    if has_model_name != has_model_provider:
        raise TracecatValidationError(
            "model_name and model_provider must be provided together"
        )
    if catalog_id := payload.get("catalog_id"):
        service = AgentPresetService(session, role=role)
        catalog_entry = await service._get_enabled_catalog_entry(catalog_id)
        payload.setdefault("model_name", catalog_entry.model_name)
        payload.setdefault("model_provider", catalog_entry.model_provider)
        return payload
    if has_model_name and has_model_provider:
        return payload

    default_model = await AgentManagementService(
        session,
        role=role,
    ).get_default_model_selection()
    if default_model is None:
        raise TracecatNotFoundError("No default model set")
    if not payload.get("model_name"):
        payload["model_name"] = default_model.model_name
    if not payload.get("model_provider"):
        payload["model_provider"] = default_model.model_provider
    # AgentPresetCreate still requires the legacy model fields. catalog_id is
    # canonical for new callers; model_name/provider are backfilled for compat.
    payload.setdefault("catalog_id", default_model.catalog_id)
    return payload


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
    results: list[AgentPresetReadMinimal] = []
    for preset in presets:
        read = AgentPresetReadMinimal.model_validate(preset)
        agents = AgentSubagentsConfig.model_validate(preset.agents or {})
        tool_approvals = cast(Mapping[str, bool] | None, preset.tool_approvals)
        capabilities = []
        if has_manual_tool_approvals(tool_approvals):
            capabilities.append("approvals")
        if agents.enabled:
            capabilities.append("subagents")
        if preset.enable_internet_access:
            capabilities.append("internet_access")
        results.append(
            read.model_copy(
                update={
                    "capabilities": capabilities,
                    "current_version_subagent_eligibility": build_subagent_eligibility(
                        agents_config=agents,
                        tool_approvals=tool_approvals,
                    ),
                }
            )
        )
    return results


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
        payload = await _create_payload_with_default_model(
            role=role,
            session=session,
            params=params,
        )
        preset = await service.create_preset(AgentPresetCreate(**payload))
        return await service.build_preset_read(preset)
    except TracecatValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except TracecatNotFoundError as exc:
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
