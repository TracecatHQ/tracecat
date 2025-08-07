"""API router for custom entities."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import ValidationError

from tracecat.auth.credentials import RoleACL
from tracecat.db.dependencies import AsyncDBSession
from tracecat.entities.models import (
    EntityDataCreate,
    EntityDataRead,
    EntityDataUpdate,
    EntityMetadataCreate,
    EntityMetadataRead,
    EntityMetadataUpdate,
    FieldMetadataCreate,
    FieldMetadataRead,
    FieldMetadataUpdate,
    QueryRequest,
    QueryResponse,
)
from tracecat.entities.service import CustomEntitiesService
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatNotFoundError

router = APIRouter(prefix="/entities", tags=["entities"])

# Dependencies
WorkspaceUser = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="yes",
    ),
]


# Entity Type Management


@router.post("/types", response_model=EntityMetadataRead)
async def create_entity_type(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    params: EntityMetadataCreate,
) -> EntityMetadataRead:
    """Create a new entity type."""
    service = CustomEntitiesService(session, role)
    try:
        entity = await service.create_entity_type(
            name=params.name,
            display_name=params.display_name,
            description=params.description,
            icon=params.icon,
            settings=params.settings,
        )
        return EntityMetadataRead.model_validate(entity, from_attributes=True)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/types", response_model=list[EntityMetadataRead])
async def list_entity_types(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    include_inactive: bool = Query(
        False, description="Include soft-deleted entity types"
    ),
) -> list[EntityMetadataRead]:
    """List all entity types."""
    service = CustomEntitiesService(session, role)
    entities = await service.list_entity_types(include_inactive=include_inactive)
    return [
        EntityMetadataRead.model_validate(e, from_attributes=True) for e in entities
    ]


@router.get("/types/{entity_id}", response_model=EntityMetadataRead)
async def get_entity_type(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    entity_id: UUID,
) -> EntityMetadataRead:
    """Get entity type by ID."""
    service = CustomEntitiesService(session, role)
    try:
        entity = await service.get_entity_type(entity_id)
        return EntityMetadataRead.model_validate(entity, from_attributes=True)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.patch("/types/{entity_id}", response_model=EntityMetadataRead)
async def update_entity_type(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    entity_id: UUID,
    params: EntityMetadataUpdate,
) -> EntityMetadataRead:
    """Update entity type display properties."""
    service = CustomEntitiesService(session, role)
    try:
        entity = await service.get_entity_type(entity_id)

        # Update mutable fields
        if params.display_name is not None:
            entity.display_name = params.display_name
        if params.description is not None:
            entity.description = params.description
        if params.icon is not None:
            entity.icon = params.icon
        if params.settings is not None:
            entity.settings = params.settings

        await service.session.commit()
        await service.session.refresh(entity)

        return EntityMetadataRead.model_validate(entity, from_attributes=True)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.delete("/types/{entity_id}")
async def deactivate_entity_type(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    entity_id: UUID,
) -> dict:
    """Soft delete entity type."""
    service = CustomEntitiesService(session, role)
    try:
        entity = await service.get_entity_type(entity_id)
        entity.is_active = False
        await service.session.commit()
        return {"message": f"Entity type {entity_id} deactivated"}
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


# Field Management


@router.post("/types/{entity_id}/fields", response_model=FieldMetadataRead)
async def create_field(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    entity_id: UUID,
    params: FieldMetadataCreate,
) -> FieldMetadataRead:
    """Create a new field for an entity type."""
    service = CustomEntitiesService(session, role)
    try:
        field = await service.create_field(
            entity_id=entity_id,
            field_key=params.field_key,
            field_type=params.field_type,
            display_name=params.display_name,
            description=params.description,
            settings=params.field_settings,
        )
        return FieldMetadataRead.model_validate(field, from_attributes=True)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/types/{entity_id}/fields", response_model=list[FieldMetadataRead])
async def list_fields(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    entity_id: UUID,
    include_inactive: bool = Query(False, description="Include soft-deleted fields"),
) -> list[FieldMetadataRead]:
    """List fields for an entity type."""
    service = CustomEntitiesService(session, role)
    fields = await service.list_fields(entity_id, include_inactive=include_inactive)
    return [FieldMetadataRead.model_validate(f, from_attributes=True) for f in fields]


@router.get("/fields/{field_id}", response_model=FieldMetadataRead)
async def get_field(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    field_id: UUID,
) -> FieldMetadataRead:
    """Get field by ID."""
    service = CustomEntitiesService(session, role)
    try:
        field = await service.get_field(field_id)
        return FieldMetadataRead.model_validate(field, from_attributes=True)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.patch("/fields/{field_id}", response_model=FieldMetadataRead)
async def update_field(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    field_id: UUID,
    params: FieldMetadataUpdate,
) -> FieldMetadataRead:
    """Update field display properties (not schema)."""
    service = CustomEntitiesService(session, role)
    try:
        field = await service.update_field_display(
            field_id=field_id,
            display_name=params.display_name,
            description=params.description,
            settings=params.field_settings,
        )
        return FieldMetadataRead.model_validate(field, from_attributes=True)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/fields/{field_id}/deactivate", response_model=FieldMetadataRead)
async def deactivate_field(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    field_id: UUID,
) -> FieldMetadataRead:
    """Soft delete field (data preserved)."""
    service = CustomEntitiesService(session, role)
    try:
        field = await service.deactivate_field(field_id)
        return FieldMetadataRead.model_validate(field, from_attributes=True)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/fields/{field_id}/reactivate", response_model=FieldMetadataRead)
async def reactivate_field(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    field_id: UUID,
) -> FieldMetadataRead:
    """Reactivate soft-deleted field."""
    service = CustomEntitiesService(session, role)
    try:
        field = await service.reactivate_field(field_id)
        return FieldMetadataRead.model_validate(field, from_attributes=True)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


# Data Operations


@router.post("/types/{entity_id}/records", response_model=EntityDataRead)
async def create_record(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    entity_id: UUID,
    data: EntityDataCreate,
) -> EntityDataRead:
    """Create a new entity record."""
    service = CustomEntitiesService(session, role)
    try:
        # Extract dynamic fields from request
        record_data = data.model_dump(exclude_unset=True)

        record = await service.create_record(entity_id, record_data)
        return EntityDataRead.model_validate(record, from_attributes=True)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/records/{record_id}", response_model=EntityDataRead)
async def get_record(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    record_id: UUID,
) -> EntityDataRead:
    """Get entity record by ID."""
    service = CustomEntitiesService(session, role)
    try:
        record = await service.get_record(record_id)
        return EntityDataRead.model_validate(record, from_attributes=True)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.patch("/records/{record_id}", response_model=EntityDataRead)
async def update_record(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    record_id: UUID,
    updates: EntityDataUpdate,
) -> EntityDataRead:
    """Update entity record."""
    service = CustomEntitiesService(session, role)
    try:
        # Extract dynamic field updates
        update_data = updates.model_dump(exclude_unset=True)

        record = await service.update_record(record_id, update_data)
        return EntityDataRead.model_validate(record, from_attributes=True)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.delete("/records/{record_id}")
async def delete_record(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    record_id: UUID,
) -> dict:
    """Delete entity record."""
    service = CustomEntitiesService(session, role)
    try:
        await service.delete_record(record_id)
        return {"message": f"Record {record_id} deleted"}
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/types/{entity_id}/query", response_model=QueryResponse)
async def query_records(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    entity_id: UUID,
    query: QueryRequest,
) -> QueryResponse:
    """Query entity records with filters."""
    service = CustomEntitiesService(session, role)
    try:
        # Convert filter models to dicts
        filters = [f.model_dump() for f in query.filters]

        records = await service.query_records(
            entity_id=entity_id,
            filters=filters,
            limit=query.limit,
            offset=query.offset,
        )

        return QueryResponse(
            records=[
                EntityDataRead.model_validate(r, from_attributes=True) for r in records
            ],
            limit=query.limit,
            offset=query.offset,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


# Schema Inspection


@router.get("/types/{entity_id}/schema")
async def get_entity_schema(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    entity_id: UUID,
) -> dict:
    """Get the dynamic schema for an entity type (for UI/validation)."""
    service = CustomEntitiesService(session, role)
    try:
        # Get entity and active fields
        entity = await service.get_entity_type(entity_id)
        fields = await service.list_fields(entity_id, include_inactive=False)

        # Build schema description
        schema = {
            "entity": {
                "id": str(entity.id),
                "name": entity.name,
                "display_name": entity.display_name,
                "description": entity.description,
            },
            "fields": [
                {
                    "key": f.field_key,
                    "type": f.field_type,
                    "display_name": f.display_name,
                    "description": f.description,
                    "required": f.is_required,
                    "settings": f.field_settings,
                }
                for f in fields
                if f.is_active
            ],
        }

        return schema
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
