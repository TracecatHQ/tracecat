from typing import Annotated

from fastapi import APIRouter, HTTPException, status
from pydantic import UUID4

from tracecat.auth.credentials import RoleACL
from tracecat.cases.entities.models import (
    CaseRecordLinkCreate,
    CaseRecordLinkRead,
    CaseRecordRead,
)
from tracecat.cases.entities.service import CaseEntitiesService
from tracecat.db.dependencies import AsyncDBSession
from tracecat.entities.models import RecordUpdate
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatNotFoundError

WorkspaceUser = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="yes",
    ),
]

router = APIRouter(prefix="/cases", tags=["cases"])


@router.get("/{case_id}/records", response_model=list[CaseRecordLinkRead])
async def list_case_records(
    role: WorkspaceUser,
    session: AsyncDBSession,
    case_id: UUID4,
    entity_id: UUID4 | None = None,
) -> list[CaseRecordLinkRead]:
    """List all records associated with a case.

    Optionally filter by entity using entity_id.
    """
    service = CaseEntitiesService(session, role=role)

    try:
        # Service returns list[CaseRecordLinkRead]
        return await service.list_records(case_id, entity_id)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e


@router.get("/{case_id}/records/{record_id}", response_model=CaseRecordRead)
async def get_case_record(
    role: WorkspaceUser,
    session: AsyncDBSession,
    case_id: UUID4,
    record_id: UUID4,
) -> CaseRecordRead:
    """Get a specific record linked to a case.

    Args:
        case_id: The case ID
        record_id: The record ID

    Returns:
        Entity record data
    """
    service = CaseEntitiesService(session, role=role)
    try:
        # Service returns RecordRead
        return await service.get_record(case_id, record_id)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e


@router.post(
    "/{case_id}/records",
    status_code=status.HTTP_201_CREATED,
    response_model=CaseRecordLinkRead,
)
async def add_record_to_case(
    role: WorkspaceUser,
    session: AsyncDBSession,
    case_id: UUID4,
    params: CaseRecordLinkCreate,
) -> CaseRecordLinkRead:
    """Associate an record with a case.

    Either provide record_id to link an existing record,
    or provide record to create a new record and link it.
    """
    service = CaseEntitiesService(session, role=role)

    try:
        if params.record_id:
            # Link existing record
            return await service.add_record(
                case_id=case_id,
                record_id=params.record_id,
                entity_id=params.entity_id,
            )
        elif params.record_data:
            # Create and link new record
            return await service.create_record(
                case_id=case_id,
                entity_id=params.entity_id,
                entity_data=params.record_data,
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Either record_id or record must be provided",
            )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e


@router.patch("/{case_id}/records/{record_id}", response_model=CaseRecordRead)
async def update_case_record(
    role: WorkspaceUser,
    session: AsyncDBSession,
    case_id: UUID4,
    record_id: UUID4,
    updates: RecordUpdate,
) -> CaseRecordRead:
    """Update an record linked to a case.

    Args:
        case_id: The case ID
        record_id: The record ID to update
        updates: Field updates

    Returns:
        Updated record
    """
    service = CaseEntitiesService(session, role=role)
    try:
        # Extract dynamic field updates and delegate
        update_data = updates.model_dump(exclude_unset=True)
        return await service.update_record(case_id, record_id, update_data)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e


@router.delete(
    "/{case_id}/record-links/{link_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_record_from_case(
    role: WorkspaceUser,
    session: AsyncDBSession,
    case_id: UUID4,
    link_id: UUID4,
) -> None:
    """Remove an record association from a case.

    This only removes the association; the record itself is preserved.
    """
    service = CaseEntitiesService(session, role=role)
    try:
        await service.remove_record(case_id, link_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e


@router.delete(
    "/{case_id}/records/{record_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_record_from_case(
    role: WorkspaceUser,
    session: AsyncDBSession,
    case_id: UUID4,
    record_id: UUID4,
) -> None:
    """Delete an record linked to a case.

    Args:
        case_id: The case ID
        record_id: The record ID to delete
    """
    service = CaseEntitiesService(session, role=role)
    try:
        await service.delete_record(case_id, record_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
