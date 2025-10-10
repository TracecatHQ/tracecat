"""Router for case duration definition endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status

from tracecat.auth.dependencies import WorkspaceUserRole
from tracecat.cases.durations.models import (
    CaseDurationCreate,
    CaseDurationRead,
    CaseDurationUpdate,
)
from tracecat.cases.durations.service import CaseDurationService
from tracecat.db.dependencies import AsyncDBSession
from tracecat.logger import logger
from tracecat.types.exceptions import (
    TracecatNotFoundError,
    TracecatValidationError,
)

router = APIRouter(prefix="/case-durations", tags=["case-durations"])


@router.get("", response_model=list[CaseDurationRead])
async def list_case_durations(
    *,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
) -> list[CaseDurationRead]:
    """List all case duration definitions for the active workspace."""
    service = CaseDurationService(session=session, role=role)
    return await service.list_definitions()


@router.get("/{duration_id}", response_model=CaseDurationRead)
async def get_case_duration(
    *,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    duration_id: uuid.UUID,
) -> CaseDurationRead:
    """Retrieve a single case duration definition."""
    service = CaseDurationService(session=session, role=role)
    try:
        return await service.get_definition(duration_id)
    except TracecatNotFoundError as err:
        logger.warning(
            "Case duration definition not found",
            duration_id=str(duration_id),
            workspace_id=str(role.workspace_id),
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(err),
        ) from err


@router.post("", response_model=CaseDurationRead, status_code=status.HTTP_201_CREATED)
async def create_case_duration(
    *,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    params: CaseDurationCreate,
) -> CaseDurationRead:
    """Create a new case duration definition."""
    service = CaseDurationService(session=session, role=role)
    try:
        return await service.create_definition(params)
    except TracecatValidationError as err:
        logger.warning(
            "Validation error creating case duration definition",
            error=str(err),
            workspace_id=str(role.workspace_id),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(err),
        ) from err
    except Exception as err:  # pragma: no cover - unexpected failure
        logger.error(
            "Failed to create case duration definition",
            error=str(err),
            error_type=type(err).__name__,
            workspace_id=str(role.workspace_id),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create case duration definition",
        ) from err


@router.patch("/{duration_id}", response_model=CaseDurationRead)
async def update_case_duration(
    *,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    duration_id: uuid.UUID,
    params: CaseDurationUpdate,
) -> CaseDurationRead:
    """Update an existing case duration definition."""
    service = CaseDurationService(session=session, role=role)
    try:
        return await service.update_definition(duration_id, params)
    except TracecatNotFoundError as err:
        logger.warning(
            "Case duration definition not found for update",
            duration_id=str(duration_id),
            workspace_id=str(role.workspace_id),
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(err),
        ) from err
    except TracecatValidationError as err:
        logger.warning(
            "Validation error updating case duration definition",
            duration_id=str(duration_id),
            error=str(err),
            workspace_id=str(role.workspace_id),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(err),
        ) from err
    except Exception as err:  # pragma: no cover - unexpected failure
        logger.error(
            "Failed to update case duration definition",
            duration_id=str(duration_id),
            error=str(err),
            error_type=type(err).__name__,
            workspace_id=str(role.workspace_id),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update case duration definition",
        ) from err


@router.delete(
    "/{duration_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None
)
async def delete_case_duration(
    *,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    duration_id: uuid.UUID,
) -> None:
    """Delete a case duration definition."""
    service = CaseDurationService(session=session, role=role)
    try:
        await service.delete_definition(duration_id)
    except TracecatNotFoundError as err:
        logger.warning(
            "Case duration definition not found for deletion",
            duration_id=str(duration_id),
            workspace_id=str(role.workspace_id),
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(err),
        ) from err
