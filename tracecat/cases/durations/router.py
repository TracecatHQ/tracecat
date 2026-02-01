"""Router for case duration definition endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status

from tracecat.auth.dependencies import WorkspaceUserRole
from tracecat.cases.durations.schemas import (
    CaseDurationCreate,
    CaseDurationDefinitionCreate,
    CaseDurationDefinitionRead,
    CaseDurationDefinitionUpdate,
    CaseDurationRead,
    CaseDurationUpdate,
)
from tracecat.cases.durations.service import (
    CaseDurationDefinitionService,
    CaseDurationService,
)
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import (
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.logger import logger

router = APIRouter(tags=["case-durations"])
definitions_router = APIRouter(prefix="/case-durations", tags=["case-durations"])
durations_router = APIRouter(
    prefix="/cases/{case_id}/durations", tags=["case-durations"]
)


@definitions_router.get("", response_model=list[CaseDurationDefinitionRead])
async def list_case_duration_definitions(
    *,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
) -> list[CaseDurationDefinitionRead]:
    """List all case duration definitions for the active workspace."""
    service = CaseDurationDefinitionService(session=session, role=role)
    return await service.list_definitions()


@definitions_router.get("/{duration_id}", response_model=CaseDurationDefinitionRead)
async def get_case_duration_definition(
    *,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    duration_id: uuid.UUID,
) -> CaseDurationDefinitionRead:
    """Retrieve a single case duration definition."""
    service = CaseDurationDefinitionService(session=session, role=role)
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


@definitions_router.post(
    "",
    response_model=CaseDurationDefinitionRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_case_duration_definition(
    *,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    params: CaseDurationDefinitionCreate,
) -> CaseDurationDefinitionRead:
    """Create a new case duration definition."""
    service = CaseDurationDefinitionService(session=session, role=role)
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


@definitions_router.patch("/{duration_id}", response_model=CaseDurationDefinitionRead)
async def update_case_duration_definition(
    *,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    duration_id: uuid.UUID,
    params: CaseDurationDefinitionUpdate,
) -> CaseDurationDefinitionRead:
    """Update an existing case duration definition."""
    service = CaseDurationDefinitionService(session=session, role=role)
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


@definitions_router.delete(
    "/{duration_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None
)
async def delete_case_duration_definition(
    *,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    duration_id: uuid.UUID,
) -> None:
    """Delete a case duration definition."""
    service = CaseDurationDefinitionService(session=session, role=role)
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


@durations_router.get("", response_model=list[CaseDurationRead])
async def list_case_durations(
    *,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    case_id: uuid.UUID,
) -> list[CaseDurationRead]:
    """Sync and list case durations for the provided case."""
    service = CaseDurationService(session=session, role=role)
    try:
        await service.sync_case_durations(case_id)
        await session.commit()
    except Exception:
        await session.rollback()
        logger.error(
            "Failed to sync case durations before listing",
            case_id=str(case_id),
        )
    try:
        return await service.list_durations(case_id)
    except TracecatNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(err),
        ) from err


@durations_router.get("/{duration_id}", response_model=CaseDurationRead)
async def get_case_duration(
    *,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    case_id: uuid.UUID,
    duration_id: uuid.UUID,
) -> CaseDurationRead:
    """Retrieve a persisted case duration."""
    service = CaseDurationService(session=session, role=role)
    try:
        return await service.get_duration(case_id, duration_id)
    except TracecatNotFoundError as err:
        logger.warning(
            "Case duration not found",
            case_id=str(case_id),
            duration_id=str(duration_id),
            workspace_id=str(role.workspace_id),
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(err),
        ) from err


@durations_router.post(
    "", response_model=CaseDurationRead, status_code=status.HTTP_201_CREATED
)
async def create_case_duration(
    *,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    case_id: uuid.UUID,
    params: CaseDurationCreate,
) -> CaseDurationRead:
    """Create a persisted case duration."""
    service = CaseDurationService(session=session, role=role)
    try:
        return await service.create_duration(case_id, params)
    except TracecatValidationError as err:
        logger.warning(
            "Validation error creating case duration",
            case_id=str(case_id),
            error=str(err),
            workspace_id=str(role.workspace_id),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(err),
        ) from err
    except TracecatNotFoundError as err:
        logger.warning(
            "Case or definition not found during duration creation",
            case_id=str(case_id),
            error=str(err),
            workspace_id=str(role.workspace_id),
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(err),
        ) from err


@durations_router.patch("/{duration_id}", response_model=CaseDurationRead)
async def update_case_duration(
    *,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    case_id: uuid.UUID,
    duration_id: uuid.UUID,
    params: CaseDurationUpdate,
) -> CaseDurationRead:
    """Update a persisted case duration."""
    service = CaseDurationService(session=session, role=role)
    try:
        return await service.update_duration(case_id, duration_id, params)
    except TracecatNotFoundError as err:
        logger.warning(
            "Case duration not found for update",
            case_id=str(case_id),
            duration_id=str(duration_id),
            workspace_id=str(role.workspace_id),
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(err),
        ) from err
    except TracecatValidationError as err:
        logger.warning(
            "Validation error updating case duration",
            case_id=str(case_id),
            duration_id=str(duration_id),
            error=str(err),
            workspace_id=str(role.workspace_id),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(err),
        ) from err


@durations_router.delete(
    "/{duration_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None
)
async def delete_case_duration(
    *,
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    case_id: uuid.UUID,
    duration_id: uuid.UUID,
) -> None:
    """Delete a persisted case duration."""
    service = CaseDurationService(session=session, role=role)
    try:
        await service.delete_duration(case_id, duration_id)
    except TracecatNotFoundError as err:
        logger.warning(
            "Case duration not found for deletion",
            case_id=str(case_id),
            duration_id=str(duration_id),
            workspace_id=str(role.workspace_id),
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(err),
        ) from err


router.include_router(definitions_router)
router.include_router(durations_router)
