"""Platform admin endpoints for managed resources."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from tracecat import config
from tracecat.auth.credentials import SuperuserRole, SuperuserStepUpRole
from tracecat.db.dependencies import AsyncDBSessionBypass
from tracecat.exceptions import (
    TracecatNotFoundError,
    TracecatSettingsError,
    TracecatValidationError,
)
from tracecat.managed_resources.source import ManagedSourceUnavailableError
from tracecat.pagination import Page, PageParams
from tracecat.registry.repositories.schemas import GitTagInfo
from tracecat.vcs.exceptions import VcsProviderError
from tracecat_ee.admin.managed_resources.schemas import (
    ManagedSourceRead,
    ManagedSourceSettingRead,
    ManagedSourceUpdate,
)
from tracecat_ee.admin.managed_resources.service import AdminManagedResourcesService

router = APIRouter(prefix="/managed-resources", tags=["admin:managed-resources"])


def provider_error_status(error: VcsProviderError) -> int:
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
        raise HTTPException(status_code=provider_error_status(e), detail=str(e)) from e
    except (TracecatSettingsError, TracecatValidationError) as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
