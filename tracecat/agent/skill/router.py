"""API router for workspace skills."""

from __future__ import annotations

import uuid
from typing import Annotated, Never

from fastapi import APIRouter, HTTPException, Query, status

from tracecat import config
from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.session.schemas import AgentSessionCreate, AgentSessionRead
from tracecat.agent.session.service import AgentSessionService
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.agent.skill.schemas import (
    SkillCreate,
    SkillDraftFileRead,
    SkillDraftPatch,
    SkillDraftRead,
    SkillPlaygroundSessionContext,
    SkillPlaygroundSessionCreate,
    SkillRead,
    SkillUpload,
    SkillUploadSessionCreate,
    SkillUploadSessionRead,
    SkillVersionRead,
)
from tracecat.agent.skill.service import SkillService
from tracecat.auth.credentials import RoleACL
from tracecat.auth.types import Role
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.pagination import CursorPaginatedResponse, CursorPaginationParams

router = APIRouter(prefix="/agent/skills", tags=["agent-skills"])

WorkspaceEditorRole = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="yes",
    ),
]


def _raise_skill_validation_error(exc: TracecatValidationError) -> Never:
    """Convert skill validation failures into the right HTTP error shape."""

    status_code = status.HTTP_400_BAD_REQUEST
    if isinstance(exc.detail, dict) and exc.detail.get("code") in {
        "skill_slug_conflict",
        "draft_revision_conflict",
        "skill_in_use",
    }:
        status_code = status.HTTP_409_CONFLICT
    raise HTTPException(
        status_code=status_code,
        detail=exc.detail if exc.detail is not None else str(exc),
    ) from exc


@router.get("", response_model=CursorPaginatedResponse[SkillRead])
@require_scope("agent:read")
async def list_skills(
    *,
    role: WorkspaceEditorRole,
    session: AsyncDBSession,
    limit: int = Query(
        default=config.TRACECAT__LIMIT_DEFAULT,
        ge=config.TRACECAT__LIMIT_MIN,
        le=config.TRACECAT__LIMIT_CURSOR_MAX,
    ),
    cursor: str | None = Query(default=None),
    reverse: bool = Query(default=False),
) -> CursorPaginatedResponse[SkillRead]:
    """List workspace skills for the current workspace."""

    service = SkillService(session, role=role)
    return await service.list_skills(
        CursorPaginationParams(limit=limit, cursor=cursor, reverse=reverse)
    )


@router.post("", response_model=SkillRead, status_code=status.HTTP_201_CREATED)
@require_scope("agent:create")
async def create_skill(
    *,
    params: SkillCreate,
    role: WorkspaceEditorRole,
    session: AsyncDBSession,
) -> SkillRead:
    """Create a new logical skill and seed its draft."""

    service = SkillService(session, role=role)
    try:
        return await service.create_skill(params)
    except TracecatValidationError as exc:
        _raise_skill_validation_error(exc)


@router.post(":upload", response_model=SkillRead, status_code=status.HTTP_201_CREATED)
@require_scope("agent:create")
async def upload_skill(
    *,
    params: SkillUpload,
    role: WorkspaceEditorRole,
    session: AsyncDBSession,
) -> SkillRead:
    """Create a new skill by importing a full draft file tree."""

    service = SkillService(session, role=role)
    try:
        return await service.upload_skill(params)
    except TracecatValidationError as exc:
        _raise_skill_validation_error(exc)


@router.get("/{skill_id}", response_model=SkillRead)
@require_scope("agent:read")
async def get_skill(
    *,
    skill_id: uuid.UUID,
    role: WorkspaceEditorRole,
    session: AsyncDBSession,
) -> SkillRead:
    """Return a skill summary including draft and current version status."""

    service = SkillService(session, role=role)
    if (skill := await service.get_skill_read(skill_id)) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Skill '{skill_id}' not found",
        )
    return skill


@router.post(
    "/{skill_id}/playground/sessions",
    response_model=AgentSessionRead,
    status_code=status.HTTP_201_CREATED,
)
@require_scope("agent:execute")
async def create_skill_playground_session(
    *,
    skill_id: uuid.UUID,
    params: SkillPlaygroundSessionCreate,
    role: WorkspaceEditorRole,
    session: AsyncDBSession,
) -> AgentSessionRead:
    """Create a transient skill playground agent session."""

    skill_service = SkillService(session, role=role)
    skill = await skill_service.get_skill(skill_id)
    if skill is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Skill '{skill_id}' not found",
        )

    version = await skill_service.get_version(params.skill_version_id)
    if version is None or version.skill_id != skill.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Skill version '{params.skill_version_id}' not found",
        )

    if any(
        value is not None
        for value in (params.model_name, params.model_provider, params.base_url)
    ) and not (params.model_name and params.model_provider):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Custom playground model requires both model_name and model_provider",
        )

    try:
        if params.mcp_integration_ids:
            await AgentPresetService(session, role=role).validate_mcp_integrations(
                params.mcp_integration_ids
            )
    except TracecatValidationError as exc:
        _raise_skill_validation_error(exc)

    session_context = SkillPlaygroundSessionContext(
        skill_id=skill.id,
        skill_slug=skill.slug,
        skill_version_id=version.id,
        model_name=params.model_name,
        model_provider=params.model_provider,
        base_url=params.base_url,
        system_prompt=params.system_prompt,
        mcp_integration_ids=params.mcp_integration_ids,
    )
    agent_session = await AgentSessionService(session, role).create_session(
        AgentSessionCreate(
            title=params.title,
            entity_type=AgentSessionEntity.SKILL,
            entity_id=skill.id,
        ),
        channel_context={
            "skill_playground": session_context.model_dump(mode="json"),
        },
    )
    return AgentSessionRead.model_validate(agent_session, from_attributes=True)


@router.get("/{skill_id}/draft", response_model=SkillDraftRead)
@require_scope("agent:read")
async def get_skill_draft(
    *,
    skill_id: uuid.UUID,
    role: WorkspaceEditorRole,
    session: AsyncDBSession,
) -> SkillDraftRead:
    """Return the mutable draft manifest for a skill."""

    service = SkillService(session, role=role)
    if (draft := await service.get_draft(skill_id)) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Skill '{skill_id}' not found",
        )
    return draft


@router.get("/{skill_id}/draft/file", response_model=SkillDraftFileRead)
@require_scope("agent:read")
async def get_skill_draft_file(
    *,
    skill_id: uuid.UUID,
    path: str = Query(...),
    role: WorkspaceEditorRole,
    session: AsyncDBSession,
) -> SkillDraftFileRead:
    """Return one draft file inline or as a presigned download."""

    service = SkillService(session, role=role)
    draft_file: SkillDraftFileRead | None
    try:
        draft_file = await service.get_draft_file(skill_id=skill_id, path=path)
    except TracecatValidationError as exc:
        _raise_skill_validation_error(exc)
    if draft_file is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Draft file '{path}' not found",
        )
    return draft_file


@router.patch("/{skill_id}/draft", response_model=SkillDraftRead)
@require_scope("agent:update")
async def patch_skill_draft(
    *,
    skill_id: uuid.UUID,
    params: SkillDraftPatch,
    role: WorkspaceEditorRole,
    session: AsyncDBSession,
) -> SkillDraftRead:
    """Apply optimistic-concurrency mutations to a draft."""

    service = SkillService(session, role=role)
    try:
        return await service.patch_draft(skill_id=skill_id, params=params)
    except TracecatValidationError as exc:
        _raise_skill_validation_error(exc)
    except TracecatNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.post(
    "/{skill_id}/draft/uploads",
    response_model=SkillUploadSessionRead,
    status_code=status.HTTP_201_CREATED,
)
@require_scope("agent:update")
async def create_skill_draft_upload(
    *,
    skill_id: uuid.UUID,
    params: SkillUploadSessionCreate,
    role: WorkspaceEditorRole,
    session: AsyncDBSession,
) -> SkillUploadSessionRead:
    """Create a staged upload session for a draft file blob."""

    service = SkillService(session, role=role)
    try:
        return await service.create_draft_upload(skill_id=skill_id, params=params)
    except TracecatValidationError as exc:
        _raise_skill_validation_error(exc)
    except TracecatNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.post("/{skill_id}/publish", response_model=SkillVersionRead)
@require_scope("agent:update")
async def publish_skill(
    *,
    skill_id: uuid.UUID,
    role: WorkspaceEditorRole,
    session: AsyncDBSession,
) -> SkillVersionRead:
    """Publish the current draft into a new immutable version."""

    service = SkillService(session, role=role)
    try:
        return await service.publish_skill(skill_id)
    except TracecatValidationError as exc:
        _raise_skill_validation_error(exc)
    except TracecatNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.get(
    "/{skill_id}/versions", response_model=CursorPaginatedResponse[SkillVersionRead]
)
@require_scope("agent:read")
async def list_skill_versions(
    *,
    skill_id: uuid.UUID,
    role: WorkspaceEditorRole,
    session: AsyncDBSession,
    limit: int = Query(
        default=config.TRACECAT__LIMIT_DEFAULT,
        ge=config.TRACECAT__LIMIT_MIN,
        le=config.TRACECAT__LIMIT_CURSOR_MAX,
    ),
    cursor: str | None = Query(default=None),
    reverse: bool = Query(default=False),
) -> CursorPaginatedResponse[SkillVersionRead]:
    """List immutable versions for a skill."""

    service = SkillService(session, role=role)
    if await service.get_skill(skill_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Skill '{skill_id}' not found",
        )
    try:
        return await service.list_versions(
            skill_id=skill_id,
            params=CursorPaginationParams(limit=limit, cursor=cursor, reverse=reverse),
        )
    except TracecatValidationError as exc:
        _raise_skill_validation_error(exc)


@router.get("/{skill_id}/versions/{version_id}", response_model=SkillVersionRead)
@require_scope("agent:read")
async def get_skill_version(
    *,
    skill_id: uuid.UUID,
    version_id: uuid.UUID,
    role: WorkspaceEditorRole,
    session: AsyncDBSession,
) -> SkillVersionRead:
    """Return one immutable skill version manifest."""

    service = SkillService(session, role=role)
    try:
        return await service.get_version_read(skill_id=skill_id, version_id=version_id)
    except TracecatNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.post("/{skill_id}/versions/{version_id}/restore", response_model=SkillDraftRead)
@require_scope("agent:update")
async def restore_skill_version(
    *,
    skill_id: uuid.UUID,
    version_id: uuid.UUID,
    role: WorkspaceEditorRole,
    session: AsyncDBSession,
) -> SkillDraftRead:
    """Replace the mutable draft with a published snapshot."""

    service = SkillService(session, role=role)
    try:
        return await service.restore_version(skill_id=skill_id, version_id=version_id)
    except TracecatNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.delete("/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("agent:delete")
async def archive_skill(
    *,
    skill_id: uuid.UUID,
    role: WorkspaceEditorRole,
    session: AsyncDBSession,
) -> None:
    """Archive a logical skill."""

    service = SkillService(session, role=role)
    try:
        await service.archive_skill(skill_id)
    except TracecatValidationError as exc:
        _raise_skill_validation_error(exc)
    except TracecatNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
