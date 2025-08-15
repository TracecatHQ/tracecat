from typing import Annotated

from fastapi import APIRouter, HTTPException, status
from pydantic import UUID4

from tracecat.auth.credentials import RoleACL
from tracecat.cases.entities.models import (
    CaseRecordLinkCreate,
    CaseRecordLinkRead,
    EntityRead,
    RecordRead,
)
from tracecat.cases.entities.service import CaseEntitiesService
from tracecat.cases.service import CasesService
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
    # Verify case exists
    cases_service = CasesService(session, role=role)
    case = await cases_service.get_case(case_id)
    if not case:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Case not found"
        )

    # List records
    service = CaseEntitiesService(session, role=role)
    links = await service.list_records(case_id, entity_id)

    # Transform to response model using model_validate
    result = []
    for link in links:
        # Use model_validate for nested models if they exist
        entity = (
            EntityRead.model_validate(link.entity, from_attributes=True)
            if link.entity
            else None
        )

        record = (
            RecordRead.model_validate(link.record, from_attributes=True)
            if link.record
            else None
        )

        result.append(
            CaseRecordLinkRead(
                id=link.id,
                case_id=link.case_id,
                entity_id=link.entity_id,
                record_id=link.record_id,
                entity=entity,
                record=record,
            )
        )

    return result


@router.get("/{case_id}/records/{record_id}", response_model=RecordRead)
async def get_case_record(
    role: WorkspaceUser,
    session: AsyncDBSession,
    case_id: UUID4,
    record_id: UUID4,
) -> RecordRead:
    """Get a specific record linked to a case.

    Args:
        case_id: The case ID
        record_id: The record ID

    Returns:
        Entity record data
    """
    # Verify case exists
    cases_service = CasesService(session, role=role)
    case = await cases_service.get_case(case_id)
    if not case:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Case not found"
        )

    service = CaseEntitiesService(session, role=role)
    try:
        record = await service.get_record(case_id, record_id)
        return RecordRead.model_validate(record, from_attributes=True)
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
    # Verify case exists
    cases_service = CasesService(session, role=role)
    case = await cases_service.get_case(case_id)
    if not case:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Case not found"
        )

    service = CaseEntitiesService(session, role=role)

    try:
        if params.record_id:
            # Link existing entity
            link = await service.add_record_to_case(
                case_id=case_id,
                record_id=params.record_id,
                entity_id=params.entity_id,
            )
            entity_record = None
        elif params.record_data:
            # Create and link new entity
            entity_record, link = await service.create_record(
                case_id=case_id,
                entity_id=params.entity_id,
                data=params.record_data,
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Either record_id or record must be provided",
            )

        # Get metadata for response - use the entities service
        from tracecat.entities.service import CustomEntitiesService

        entities_service = CustomEntitiesService(session, role=role)
        try:
            entity = await entities_service.get_entity_type(params.entity_id)
        except Exception:
            entity = None

        # Use model_validate for nested models
        metadata_read = (
            EntityRead.model_validate(entity, from_attributes=True) if entity else None
        )

        # For record, we need to handle the case where we created a new record
        # or are linking an existing one
        data_read = None
        if entity_record:
            # We created a new record
            data_read = RecordRead.model_validate(entity_record, from_attributes=True)
        elif link.record_id:
            # We're linking to an existing record - fetch it
            try:
                existing_record = await service.get_record(case_id, link.record_id)
                data_read = RecordRead.model_validate(
                    existing_record, from_attributes=True
                )
            except Exception:
                # Fallback to minimal response
                data_read = RecordRead(
                    id=link.record_id,
                    entity_id=params.entity_id,
                    field_data={},
                )

        return CaseRecordLinkRead(
            id=link.id,
            case_id=link.case_id,
            entity_id=link.entity_id,
            record_id=link.record_id,
            entity=metadata_read,
            record=data_read,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to associate entity with case",
        ) from e


@router.patch("/{case_id}/records/{record_id}", response_model=RecordRead)
async def update_case_record(
    role: WorkspaceUser,
    session: AsyncDBSession,
    case_id: UUID4,
    record_id: UUID4,
    updates: RecordUpdate,
) -> RecordRead:
    """Update an record linked to a case.

    Args:
        case_id: The case ID
        record_id: The record ID to update
        updates: Field updates

    Returns:
        Updated record
    """
    # Verify case exists
    cases_service = CasesService(session, role=role)
    case = await cases_service.get_case(case_id)
    if not case:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Case not found"
        )

    service = CaseEntitiesService(session, role=role)
    try:
        # Extract dynamic field updates
        update_data = updates.model_dump(exclude_unset=True)
        updated_record = await service.update_record(case_id, record_id, update_data)
        return RecordRead.model_validate(updated_record, from_attributes=True)
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
    # Verify case exists
    cases_service = CasesService(session, role=role)
    case = await cases_service.get_case(case_id)
    if not case:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Case not found"
        )

    service = CaseEntitiesService(session, role=role)
    try:
        await service.remove_record(case_id, link_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
