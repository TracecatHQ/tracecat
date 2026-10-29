"""Platform admin endpoints for managed resources."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from tracecat import config
from tracecat.auth.credentials import SuperuserRole, SuperuserStepUpRole
from tracecat.db.dependencies import AsyncDBSessionBypass
from tracecat.exceptions import (
    TracecatConflictError,
    TracecatNotFoundError,
    TracecatSettingsError,
    TracecatValidationError,
)
from tracecat.managed_resources.schemas import ManagedRolloutRead
from tracecat.managed_resources.source import ManagedSourceUnavailableError
from tracecat.pagination import Page, PageParams
from tracecat.registry.repositories.schemas import GitTagInfo
from tracecat.vcs.exceptions import VcsProviderError
from tracecat_ee.admin.managed_resources.schemas import (
    ManagedReferenceMatchesUpdate,
    ManagedReferenceMatchItem,
    ManagedReleaseCandidateRead,
    ManagedReleaseCreate,
    ManagedRolloutApplyRequest,
    ManagedRolloutPreviewRequest,
    ManagedSourceRead,
    ManagedSourceSettingRead,
    ManagedSourceUpdate,
    ManagedTargetCreate,
    ManagedTargetRead,
    ManagedTargetUpdate,
    ManagedWorkspaceRead,
)
from tracecat_ee.admin.managed_resources.service import AdminManagedResourcesService

router = APIRouter(prefix="/managed-resources", tags=["admin:managed-resources"])


def _provider_error_status(error: VcsProviderError) -> int:
    """Choose the response status for a VCS provider failure.

    Errors without an upstream status (missing credentials, an app that is not
    installed) stay 400, as in the workspace sync routes. A missing repository
    is 404. When the provider rejects the source organization's credentials,
    rate limits, or fails, the caller cannot fix the request, so it is 502.
    """
    match error.status_code:
        case 404:
            return status.HTTP_404_NOT_FOUND
        case 401 | 403 | 429:
            return status.HTTP_502_BAD_GATEWAY
        case int(code) if code >= 500:
            return status.HTTP_502_BAD_GATEWAY
        case _:
            return status.HTTP_400_BAD_REQUEST


@router.get("/source", response_model=ManagedSourceSettingRead)
async def get_source(
    role: SuperuserRole,
    session: AsyncDBSessionBypass,
) -> ManagedSourceSettingRead:
    """Get the workspace managed resources are published from."""
    service = AdminManagedResourcesService(session, role)
    try:
        return await service.get_source()
    except ManagedSourceUnavailableError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e


@router.put("/source", response_model=ManagedSourceRead)
async def set_source(
    role: SuperuserStepUpRole,
    session: AsyncDBSessionBypass,
    params: ManagedSourceUpdate,
) -> ManagedSourceRead:
    """Set the managed resources source and pin its Git binding."""
    service = AdminManagedResourcesService(session, role)
    try:
        return await service.set_source(params)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except TracecatValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e


@router.delete("/source", status_code=status.HTTP_204_NO_CONTENT)
async def clear_source(
    role: SuperuserStepUpRole,
    session: AsyncDBSessionBypass,
) -> None:
    """Remove the managed resources source setting."""
    await AdminManagedResourcesService(session, role).clear_source()


@router.get("/source/tags", response_model=Page[GitTagInfo])
async def list_source_tags(
    role: SuperuserRole,
    session: AsyncDBSessionBypass,
    limit: Annotated[
        int, Query(ge=config.TRACECAT__LIMIT_MIN, le=config.TRACECAT__LIMIT_CURSOR_MAX)
    ] = config.TRACECAT__LIMIT_DEFAULT,
    cursor: Annotated[str | None, Query(max_length=8192)] = None,
) -> Page[GitTagInfo]:
    """List tags in the managed resources source repository, newest first."""
    service = AdminManagedResourcesService(session, role)
    try:
        return await service.list_source_tags(PageParams(limit=limit, cursor=cursor))
    except ManagedSourceUnavailableError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    except VcsProviderError as e:
        raise HTTPException(status_code=_provider_error_status(e), detail=str(e)) from e
    except (TracecatSettingsError, TracecatValidationError) as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e


_Limit = Annotated[
    int, Query(ge=config.TRACECAT__LIMIT_MIN, le=config.TRACECAT__LIMIT_CURSOR_MAX)
]
_Cursor = Annotated[str | None, Query(max_length=8192)]


def _domain_error(error: Exception) -> HTTPException:
    """Map a managed resources domain error to its HTTP status."""
    match error:
        case ManagedSourceUnavailableError():
            code = status.HTTP_409_CONFLICT
        case TracecatNotFoundError():
            code = status.HTTP_404_NOT_FOUND
        case TracecatConflictError():
            code = status.HTTP_409_CONFLICT
        case VcsProviderError():
            code = _provider_error_status(error)
        case _:
            code = status.HTTP_422_UNPROCESSABLE_CONTENT
    return HTTPException(status_code=code, detail=str(error))


_DOMAIN_ERRORS = (
    ManagedSourceUnavailableError,
    TracecatNotFoundError,
    TracecatConflictError,
    TracecatValidationError,
    VcsProviderError,
)


@router.get("/source/release-candidate", response_model=ManagedReleaseCandidateRead)
async def get_release_candidate(
    role: SuperuserRole,
    session: AsyncDBSessionBypass,
) -> ManagedReleaseCandidateRead:
    """Describe the commit a new release would tag and the package in it."""
    try:
        return await AdminManagedResourcesService(session, role).release_candidate()
    except _DOMAIN_ERRORS as e:
        raise _domain_error(e) from e


@router.post("/source/releases", response_model=GitTagInfo, status_code=201)
async def cut_release(
    role: SuperuserStepUpRole,
    session: AsyncDBSessionBypass,
    params: ManagedReleaseCreate,
) -> GitTagInfo:
    """Tag the source repository's base branch HEAD as a new release."""
    try:
        return await AdminManagedResourcesService(session, role).cut_release(params)
    except _DOMAIN_ERRORS as e:
        raise _domain_error(e) from e


@router.get("/workspaces", response_model=Page[ManagedWorkspaceRead])
async def list_target_candidates(
    role: SuperuserRole,
    session: AsyncDBSessionBypass,
    organization_id: uuid.UUID | None = None,
    limit: _Limit = config.TRACECAT__LIMIT_DEFAULT,
    cursor: _Cursor = None,
) -> Page[ManagedWorkspaceRead]:
    """List workspaces in every organization, marking targets and the source."""
    return await AdminManagedResourcesService(session, role).list_workspaces(
        PageParams(limit=limit, cursor=cursor), organization_id=organization_id
    )


@router.get("/targets", response_model=Page[ManagedTargetRead])
async def list_targets(
    role: SuperuserRole,
    session: AsyncDBSessionBypass,
    limit: _Limit = config.TRACECAT__LIMIT_DEFAULT,
    cursor: _Cursor = None,
) -> Page[ManagedTargetRead]:
    """List target workspaces with their pinned tag and last rollout."""
    return await AdminManagedResourcesService(session, role).list_targets(
        PageParams(limit=limit, cursor=cursor)
    )


@router.post("/targets", response_model=ManagedTargetRead, status_code=201)
async def assign_target(
    role: SuperuserStepUpRole,
    session: AsyncDBSessionBypass,
    params: ManagedTargetCreate,
) -> ManagedTargetRead:
    """Assign a workspace as a target pinned to a tag; a rollout applies it."""
    try:
        return await AdminManagedResourcesService(session, role).assign_target(params)
    except _DOMAIN_ERRORS as e:
        raise _domain_error(e) from e


@router.patch("/targets/{target_id}", response_model=ManagedTargetRead)
async def update_target(
    role: SuperuserStepUpRole,
    session: AsyncDBSessionBypass,
    target_id: uuid.UUID,
    params: ManagedTargetUpdate,
) -> ManagedTargetRead:
    """Move a target's pinned tag."""
    try:
        return await AdminManagedResourcesService(session, role).update_target(
            target_id, params
        )
    except _DOMAIN_ERRORS as e:
        raise _domain_error(e) from e


@router.post("/targets/{target_id}/unassign", response_model=ManagedRolloutRead)
async def unassign_target(
    role: SuperuserStepUpRole,
    session: AsyncDBSessionBypass,
    target_id: uuid.UUID,
) -> ManagedRolloutRead:
    """Preview unassigning a target; applying it deletes its managed resources."""
    try:
        return await AdminManagedResourcesService(session, role).unassign_target(
            target_id
        )
    except _DOMAIN_ERRORS as e:
        raise _domain_error(e) from e


@router.get(
    "/targets/{target_id}/references",
    response_model=list[ManagedReferenceMatchItem],
)
async def list_reference_matches(
    role: SuperuserRole,
    session: AsyncDBSessionBypass,
    target_id: uuid.UUID,
) -> list[ManagedReferenceMatchItem]:
    """List a target's saved model, MCP integration and secret store matches."""
    try:
        return await AdminManagedResourcesService(session, role).list_references(
            target_id
        )
    except _DOMAIN_ERRORS as e:
        raise _domain_error(e) from e


@router.put(
    "/targets/{target_id}/references",
    response_model=list[ManagedReferenceMatchItem],
)
async def save_reference_matches(
    role: SuperuserStepUpRole,
    session: AsyncDBSessionBypass,
    target_id: uuid.UUID,
    params: ManagedReferenceMatchesUpdate,
) -> list[ManagedReferenceMatchItem]:
    """Save reference matches; later version bumps reuse them."""
    try:
        return await AdminManagedResourcesService(session, role).save_references(
            target_id, params
        )
    except _DOMAIN_ERRORS as e:
        raise _domain_error(e) from e


@router.post("/rollouts/preview", response_model=list[ManagedRolloutRead])
async def preview_rollouts(
    role: SuperuserStepUpRole,
    session: AsyncDBSessionBypass,
    params: ManagedRolloutPreviewRequest,
) -> list[ManagedRolloutRead]:
    """Start a preview of a tag for each target; poll the rollouts for results."""
    try:
        return await AdminManagedResourcesService(session, role).preview_rollouts(
            params
        )
    except _DOMAIN_ERRORS as e:
        raise _domain_error(e) from e


@router.post("/rollouts/apply", response_model=list[ManagedRolloutRead])
async def apply_rollouts(
    role: SuperuserStepUpRole,
    session: AsyncDBSessionBypass,
    params: ManagedRolloutApplyRequest,
) -> list[ManagedRolloutRead]:
    """Apply the ready rollouts among those given; the rest are left as they are."""
    try:
        return await AdminManagedResourcesService(session, role).apply_rollouts(params)
    except _DOMAIN_ERRORS as e:
        raise _domain_error(e) from e


@router.get("/rollouts", response_model=Page[ManagedRolloutRead])
async def list_rollouts(
    role: SuperuserRole,
    session: AsyncDBSessionBypass,
    target_id: uuid.UUID | None = None,
    limit: _Limit = config.TRACECAT__LIMIT_DEFAULT,
    cursor: _Cursor = None,
) -> Page[ManagedRolloutRead]:
    """List rollout records, newest first."""
    return await AdminManagedResourcesService(session, role).list_rollouts(
        PageParams(limit=limit, cursor=cursor), target_id=target_id
    )


@router.get("/rollouts/{rollout_id}", response_model=ManagedRolloutRead)
async def get_rollout(
    role: SuperuserRole,
    session: AsyncDBSessionBypass,
    rollout_id: uuid.UUID,
) -> ManagedRolloutRead:
    """Get one rollout record with its preview."""
    try:
        return await AdminManagedResourcesService(session, role).get_rollout(rollout_id)
    except _DOMAIN_ERRORS as e:
        raise _domain_error(e) from e
