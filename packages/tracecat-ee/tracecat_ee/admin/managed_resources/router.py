"""Platform admin endpoints for managed resources."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from tracecat import config
from tracecat.auth.credentials import SuperuserRole
from tracecat.db.dependencies import AsyncDBSessionBypass
from tracecat.exceptions import TracecatSettingsError, TracecatValidationError
from tracecat.managed_resources.source import (
    ManagedSourceInvalidError,
    ManagedSourceNotConfiguredError,
)
from tracecat.pagination import Page, PageParams
from tracecat.registry.repositories.schemas import GitTagInfo
from tracecat.vcs.exceptions import VcsProviderError
from tracecat_ee.admin.managed_resources.service import AdminManagedResourcesService

router = APIRouter(prefix="/managed-resources", tags=["admin:managed-resources"])


def provider_error_status(error: VcsProviderError) -> int:
    """Choose the response status for a VCS provider failure.

    Errors without an upstream status (missing credentials, an app that is not
    installed) stay 400, as in the workspace sync routes. A missing repository
    or tag is 404. When the provider rejects the source organization's
    credentials, rate limits, or fails, the caller cannot fix the request, so
    the response is 502.
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
    except (ManagedSourceNotConfiguredError, ManagedSourceInvalidError) as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    except VcsProviderError as e:
        raise HTTPException(status_code=provider_error_status(e), detail=str(e)) from e
    except (TracecatSettingsError, TracecatValidationError) as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
