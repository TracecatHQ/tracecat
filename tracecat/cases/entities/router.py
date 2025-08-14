from typing import Annotated

from fastapi import APIRouter, HTTPException, status
from pydantic import UUID4

from tracecat.auth.credentials import RoleACL
from tracecat.cases.entities.models import (
    CaseEntityLinkCreate,
    CaseEntityLinkRead,
    EntityDataRead,
    EntityMetadataRead,
    EntityTypeListRead,
)
from tracecat.cases.entities.service import CaseEntitiesService
from tracecat.cases.service import CasesService
from tracecat.db.dependencies import AsyncDBSession
from tracecat.types.auth import Role

WorkspaceUser = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="yes",
    ),
]

router = APIRouter(prefix="/cases", tags=["cases"])


@router.get("/{case_id}/entities", response_model=list[CaseEntityLinkRead])
async def list_case_entities(
    role: WorkspaceUser,
    session: AsyncDBSession,
    case_id: UUID4,
    entity_metadata_id: UUID4 | None = None,
) -> list[CaseEntityLinkRead]:
    """List all entity records associated with a case.

    Optionally filter by entity type using entity_metadata_id.
    """
    # Verify case exists
    cases_service = CasesService(session, role=role)
    case = await cases_service.get_case(case_id)
    if not case:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Case not found"
        )

    # List entities
    service = CaseEntitiesService(session, role=role)
    links = await service.list_entities_for_case(case_id, entity_metadata_id)

    # Transform to response model using model_validate
    result = []
    for link in links:
        # Use model_validate for nested models if they exist
        entity_metadata = (
            EntityMetadataRead.model_validate(
                link.entity_metadata, from_attributes=True
            )
            if link.entity_metadata
            else None
        )

        entity_data = (
            EntityDataRead.model_validate(link.entity_data, from_attributes=True)
            if link.entity_data
            else None
        )

        result.append(
            CaseEntityLinkRead(
                id=link.id,
                case_id=link.case_id,
                entity_metadata_id=link.entity_metadata_id,
                entity_data_id=link.entity_data_id,
                entity_metadata=entity_metadata,
                entity_data=entity_data,
            )
        )

    return result


@router.get("/entity-types", response_model=list[EntityTypeListRead])
async def list_available_entity_types(
    role: WorkspaceUser,
    session: AsyncDBSession,
) -> list[EntityTypeListRead]:
    """List all available entity types in the workspace."""
    service = CaseEntitiesService(session, role=role)
    entity_types = await service.list_available_entity_types()

    return [
        EntityTypeListRead.model_validate(entity, from_attributes=True)
        for entity in entity_types
    ]


@router.post(
    "/{case_id}/entities",
    status_code=status.HTTP_201_CREATED,
    response_model=CaseEntityLinkRead,
)
async def associate_entity_with_case(
    role: WorkspaceUser,
    session: AsyncDBSession,
    case_id: UUID4,
    params: CaseEntityLinkCreate,
) -> CaseEntityLinkRead:
    """Associate an entity record with a case.

    Either provide entity_data_id to link an existing record,
    or provide entity_data to create a new record and link it.
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
        if params.entity_data_id:
            # Link existing entity
            link = await service.associate_entity_with_case(
                case_id=case_id,
                entity_data_id=params.entity_data_id,
                entity_metadata_id=params.entity_metadata_id,
            )
            entity_record = None
        elif params.entity_data:
            # Create and link new entity
            entity_record, link = await service.create_and_associate_entity(
                case_id=case_id,
                entity_metadata_id=params.entity_metadata_id,
                entity_data=params.entity_data,
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Either entity_data_id or entity_data must be provided",
            )

        # Get metadata for response
        entity_metadata = await service.get_entity_metadata(params.entity_metadata_id)

        # Use model_validate for nested models
        metadata_read = (
            EntityMetadataRead.model_validate(entity_metadata, from_attributes=True)
            if entity_metadata
            else None
        )

        # For entity_data, we need to handle the case where we created a new record
        # or are linking an existing one
        data_read = None
        if entity_record:
            # We created a new record
            data_read = EntityDataRead.model_validate(
                entity_record, from_attributes=True
            )
        elif link.entity_data_id:
            # We're linking to an existing record, but we don't have the full data
            # We could fetch it, but for now we'll create a minimal response
            # This could be improved by fetching the actual entity data
            data_read = EntityDataRead(
                id=link.entity_data_id,
                entity_metadata_id=params.entity_metadata_id,
                field_data={},
            )

        return CaseEntityLinkRead(
            id=link.id,
            case_id=link.case_id,
            entity_metadata_id=link.entity_metadata_id,
            entity_data_id=link.entity_data_id,
            entity_metadata=metadata_read,
            entity_data=data_read,
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


@router.delete(
    "/{case_id}/entities/{link_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_entity_association(
    role: WorkspaceUser,
    session: AsyncDBSession,
    case_id: UUID4,
    link_id: UUID4,
) -> None:
    """Remove an entity association from a case.

    This only removes the association; the entity record itself is preserved.
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
        await service.remove_entity_association(case_id, link_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
