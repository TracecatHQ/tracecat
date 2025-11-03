import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, status

from tracecat.agent.profiles.schemas import (
    AgentProfileCreate,
    AgentProfileRead,
    AgentProfileUpdate,
)
from tracecat.agent.service import AgentManagementService
from tracecat.auth.credentials import RoleACL
from tracecat.auth.types import Role
from tracecat.authz.enums import WorkspaceRole
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError

router = APIRouter(prefix="/agent/profiles", tags=["agent-profiles"])

WorkspaceEditorRole = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="yes",
        require_workspace_roles=[WorkspaceRole.EDITOR, WorkspaceRole.ADMIN],
    ),
]


@router.get("", response_model=list[AgentProfileRead])
async def list_agent_profiles(
    *,
    role: WorkspaceEditorRole,
    session: AsyncDBSession,
) -> list[AgentProfileRead]:
    """List all agent profiles for the current workspace."""

    service = AgentManagementService(session, role=role)
    return await service.list_agent_profiles()


@router.post(
    "",
    response_model=AgentProfileRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_agent_profile(
    *,
    params: AgentProfileCreate,
    role: WorkspaceEditorRole,
    session: AsyncDBSession,
) -> AgentProfileRead:
    """Create a new agent profile."""

    service = AgentManagementService(session, role=role)
    try:
        return await service.create_agent_profile(params)
    except TracecatValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e


@router.get("/{profile_id}", response_model=AgentProfileRead)
async def get_agent_profile(
    *,
    profile_id: uuid.UUID,
    role: WorkspaceEditorRole,
    session: AsyncDBSession,
) -> AgentProfileRead:
    """Retrieve an agent profile by ID."""

    service = AgentManagementService(session, role=role)
    try:
        return await service.get_agent_profile(profile_id)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent profile {profile_id} not found",
        ) from e


@router.get("/by-slug/{slug}", response_model=AgentProfileRead)
async def get_agent_profile_by_slug(
    *,
    slug: str,
    role: WorkspaceEditorRole,
    session: AsyncDBSession,
) -> AgentProfileRead:
    """Retrieve an agent profile by slug."""

    service = AgentManagementService(session, role=role)
    try:
        return await service.get_agent_profile_by_slug(slug)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent profile '{slug}' not found",
        ) from e


@router.patch("/{profile_id}", response_model=AgentProfileRead)
async def update_agent_profile(
    *,
    profile_id: uuid.UUID,
    params: AgentProfileUpdate,
    role: WorkspaceEditorRole,
    session: AsyncDBSession,
) -> AgentProfileRead:
    """Update an existing agent profile."""

    service = AgentManagementService(session, role=role)
    try:
        return await service.update_agent_profile(profile_id, params)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent profile {profile_id} not found",
        ) from e
    except TracecatValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e


@router.delete("/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent_profile(
    *,
    profile_id: uuid.UUID,
    role: WorkspaceEditorRole,
    session: AsyncDBSession,
) -> None:
    """Delete an agent profile."""

    service = AgentManagementService(session, role=role)
    try:
        await service.delete_agent_profile(profile_id)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent profile {profile_id} not found",
        ) from e
