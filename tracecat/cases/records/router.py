"""Router for case records endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, status

from tracecat.auth.credentials import RoleACL
from tracecat.cases.records.schemas import (
    CaseRecordCreate,
    CaseRecordDeleteResponse,
    CaseRecordLink,
    CaseRecordListResponse,
    CaseRecordRead,
    CaseRecordUpdate,
)
from tracecat.cases.records.service import CaseRecordService
from tracecat.cases.service import CasesService
from tracecat.db.dependencies import AsyncDBSession
from tracecat.logger import logger
from tracecat.types.auth import Role
from tracecat.types.exceptions import (
    TracecatNotFoundError,
    TracecatValidationError,
)

router = APIRouter(tags=["case-records"], prefix="/cases/{case_id}/records")

WorkspaceUser = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="yes",
    ),
]


@router.get("", response_model=CaseRecordListResponse)
async def list_case_records(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    case_id: uuid.UUID,
) -> CaseRecordListResponse:
    """List all records for a case."""
    cases_service = CasesService(session, role)
    case = await cases_service.get_case(case_id)
    if case is None:
        logger.warning("Case not found", case_id=case_id, user_id=role.user_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case with ID {case_id} not found",
        )

    service = CaseRecordService(session, role)
    records = await service.list_case_records(case)

    items = [
        CaseRecordRead(
            id=record.id,
            case_id=record.case_id,
            entity_id=record.entity_id,
            record_id=record.record_id,
            entity_key=record.entity.key,
            entity_display_name=record.entity.display_name,
            data=record.record.data,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )
        for record in records
    ]

    return CaseRecordListResponse(items=items, total=len(items))


@router.get("/{case_record_id}", response_model=CaseRecordRead)
async def get_case_record(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    case_id: uuid.UUID,
    case_record_id: uuid.UUID,
) -> CaseRecordRead:
    """Get a specific case record."""
    cases_service = CasesService(session, role)
    case = await cases_service.get_case(case_id)
    if case is None:
        logger.warning("Case not found", case_id=case_id, user_id=role.user_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case with ID {case_id} not found",
        )

    service = CaseRecordService(session, role)
    record = await service.get_case_record(case, case_record_id)
    if record is None:
        logger.warning(
            "Case record not found",
            case_id=case_id,
            case_record_id=case_record_id,
            user_id=role.user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case record with ID {case_record_id} not found",
        )

    return CaseRecordRead(
        id=record.id,
        case_id=record.case_id,
        entity_id=record.entity_id,
        record_id=record.record_id,
        entity_key=record.entity.key,
        entity_display_name=record.entity.display_name,
        data=record.record.data,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


@router.post("", status_code=status.HTTP_201_CREATED, response_model=CaseRecordRead)
async def create_case_record(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    case_id: uuid.UUID,
    params: CaseRecordCreate,
) -> CaseRecordRead:
    """Create a new entity record and link it to the case."""
    cases_service = CasesService(session, role)
    case = await cases_service.get_case(case_id)
    if case is None:
        logger.warning("Case not found", case_id=case_id, user_id=role.user_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case with ID {case_id} not found",
        )

    service = CaseRecordService(session, role)
    try:
        record = await service.create_case_record(case, params)
    except TracecatNotFoundError as e:
        logger.warning(
            "Entity not found",
            case_id=case_id,
            entity_key=params.entity_key,
            error=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    except TracecatValidationError as e:
        logger.warning(
            "Validation error creating case record",
            case_id=case_id,
            error=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except Exception as e:
        logger.error(
            "Failed to create case record",
            case_id=case_id,
            entity_key=params.entity_key,
            error=str(e),
            error_type=type(e).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create case record",
        ) from e

    return CaseRecordRead(
        id=record.id,
        case_id=record.case_id,
        entity_id=record.entity_id,
        record_id=record.record_id,
        entity_key=record.entity.key,
        entity_display_name=record.entity.display_name,
        data=record.record.data,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


@router.patch("/link", status_code=status.HTTP_200_OK, response_model=CaseRecordRead)
async def link_entity_record(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    case_id: uuid.UUID,
    params: CaseRecordLink,
) -> CaseRecordRead:
    """Link an existing entity record to the case."""
    cases_service = CasesService(session, role)
    case = await cases_service.get_case(case_id)
    if case is None:
        logger.warning("Case not found", case_id=case_id, user_id=role.user_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case with ID {case_id} not found",
        )

    service = CaseRecordService(session, role)
    try:
        record = await service.link_entity_record(case, params)
    except TracecatNotFoundError as e:
        logger.warning(
            "Entity record not found",
            case_id=case_id,
            record_id=params.entity_record_id,
            error=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    except TracecatValidationError as e:
        logger.warning(
            "Validation error linking record",
            case_id=case_id,
            record_id=params.entity_record_id,
            error=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except Exception as e:
        logger.error(
            "Failed to link entity record",
            case_id=case_id,
            record_id=params.entity_record_id,
            error=str(e),
            error_type=type(e).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to link entity record",
        ) from e

    return CaseRecordRead(
        id=record.id,
        case_id=record.case_id,
        entity_id=record.entity_id,
        record_id=record.record_id,
        entity_key=record.entity.key,
        entity_display_name=record.entity.display_name,
        data=record.record.data,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


@router.patch("/{case_record_id}", response_model=CaseRecordRead)
async def update_case_record(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    case_id: uuid.UUID,
    case_record_id: uuid.UUID,
    params: CaseRecordUpdate,
) -> CaseRecordRead:
    """Update the entity record data for a case record."""
    cases_service = CasesService(session, role)
    case = await cases_service.get_case(case_id)
    if case is None:
        logger.warning("Case not found", case_id=case_id, user_id=role.user_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case with ID {case_id} not found",
        )

    service = CaseRecordService(session, role)
    record = await service.get_case_record(case, case_record_id)
    if record is None:
        logger.warning(
            "Case record not found",
            case_id=case_id,
            case_record_id=case_record_id,
            user_id=role.user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case record with ID {case_record_id} not found",
        )

    try:
        updated_record = await service.update_case_record(record, params)
    except Exception as e:
        logger.error(
            "Failed to update case record",
            case_id=case_id,
            case_record_id=case_record_id,
            error=str(e),
            error_type=type(e).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update case record",
        ) from e

    return CaseRecordRead(
        id=updated_record.id,
        case_id=updated_record.case_id,
        entity_id=updated_record.entity_id,
        record_id=updated_record.record_id,
        entity_key=updated_record.entity.key,
        entity_display_name=updated_record.entity.display_name,
        data=updated_record.record.data,
        created_at=updated_record.created_at,
        updated_at=updated_record.updated_at,
    )


@router.patch(
    "/{case_record_id}/unlink",
    status_code=status.HTTP_200_OK,
)
async def unlink_case_record(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    case_id: uuid.UUID,
    case_record_id: uuid.UUID,
) -> CaseRecordDeleteResponse:
    """Unlink a record from a case (soft delete - removes link only)."""
    cases_service = CasesService(session, role)
    case = await cases_service.get_case(case_id)
    if case is None:
        logger.warning("Case not found", case_id=case_id, user_id=role.user_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case with ID {case_id} not found",
        )

    service = CaseRecordService(session, role)
    record = await service.get_case_record(case, case_record_id)
    if record is None:
        logger.warning(
            "Case record not found",
            case_id=case_id,
            case_record_id=case_record_id,
            user_id=role.user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case record with ID {case_record_id} not found",
        )

    try:
        await service.unlink_case_record(record)
    except Exception as e:
        logger.error(
            "Failed to unlink case record",
            case_id=case_id,
            case_record_id=case_record_id,
            error=str(e),
            error_type=type(e).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to unlink case record",
        ) from e
    return CaseRecordDeleteResponse(
        action="unlink",
        case_id=case_id,
        record_id=record.record_id,
        case_record_id=case_record_id,
    )


@router.delete(
    "/{case_record_id}",
    status_code=status.HTTP_200_OK,
)
async def delete_case_record(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    case_id: uuid.UUID,
    case_record_id: uuid.UUID,
) -> CaseRecordDeleteResponse:
    """Delete a case record and its entity record (hard delete)."""
    cases_service = CasesService(session, role)
    case = await cases_service.get_case(case_id)
    if case is None:
        logger.warning("Case not found", case_id=case_id, user_id=role.user_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case with ID {case_id} not found",
        )

    service = CaseRecordService(session, role)
    record = await service.get_case_record(case, case_record_id)
    if record is None:
        logger.warning(
            "Case record not found",
            case_id=case_id,
            case_record_id=case_record_id,
            user_id=role.user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Case record with ID {case_record_id} not found",
        )

    try:
        await service.delete_case_record(record)
    except Exception as e:
        logger.error(
            "Failed to delete case record",
            case_id=case_id,
            case_record_id=case_record_id,
            error=str(e),
            error_type=type(e).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete case record",
        ) from e
    return CaseRecordDeleteResponse(
        action="delete",
        case_id=case_id,
        record_id=record.record_id,
        case_record_id=case_record_id,
    )
