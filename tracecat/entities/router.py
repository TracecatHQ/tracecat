"""API router for custom entities."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from tracecat.auth.credentials import RoleACL
from tracecat.db.dependencies import AsyncDBSession
from tracecat.entities.models import (
    EntityCreate,
    EntityRead,
    EntitySchemaField,
    EntitySchemaInfo,
    EntitySchemaResponse,
    EntityUpdate,
    FieldMetadataCreate,
    FieldMetadataRead,
    FieldMetadataUpdate,
    QueryRequest,
    QueryResponse,
    RecordCreate,
    RecordRead,
    RecordUpdate,
    RelationDefinitionCreate,
    RelationDefinitionCreateGlobal,
    RelationDefinitionRead,
    RelationDefinitionUpdate,
)
from tracecat.entities.service import CustomEntitiesService
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatNotFoundError
from tracecat.types.pagination import CursorPaginatedResponse, CursorPaginationParams

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


@router.post("/types", response_model=EntityRead)
async def create_entity(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    params: EntityCreate,
) -> EntityRead:
    """Create a new entity."""
    service = CustomEntitiesService(session, role)
    try:
        entity = await service.create_entity(
            name=params.name,
            display_name=params.display_name,
            description=params.description,
            icon=params.icon,
        )
        return EntityRead.model_validate(entity, from_attributes=True)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/types", response_model=list[EntityRead])
async def list_entities(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    include_inactive: bool = Query(False, description="Include soft-deleted entities"),
) -> list[EntityRead]:
    """List all entities."""
    service = CustomEntitiesService(session, role)
    entities = await service.list_entities(include_inactive=include_inactive)
    return [EntityRead.model_validate(e, from_attributes=True) for e in entities]


@router.get("/types/{entity_id}", response_model=EntityRead)
async def get_entity(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    entity_id: UUID,
) -> EntityRead:
    """Get entity by ID."""
    service = CustomEntitiesService(session, role)
    try:
        entity = await service.get_entity(entity_id)
        return EntityRead.model_validate(entity, from_attributes=True)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.patch("/types/{entity_id}", response_model=EntityRead)
async def update_entity(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    entity_id: UUID,
    params: EntityUpdate,
) -> EntityRead:
    """Update entity display properties."""
    service = CustomEntitiesService(session, role)
    try:
        entity = await service.update_entity(
            entity_id=entity_id,
            display_name=params.display_name,
            description=params.description,
            icon=params.icon,
        )
        return EntityRead.model_validate(entity, from_attributes=True)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/types/{entity_id}/archive", response_model=EntityRead)
async def archive_entity(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    entity_id: UUID,
) -> EntityRead:
    """Archive (soft delete) entity.

    Cascades to all fields and relations owned by the entity.
    """
    service = CustomEntitiesService(session, role)
    try:
        entity = await service.deactivate_entity(entity_id)
        return EntityRead.model_validate(entity, from_attributes=True)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/types/{entity_id}/restore", response_model=EntityRead)
async def restore_entity(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    entity_id: UUID,
) -> EntityRead:
    """Restore archived entity.

    Cascades to all fields and relations owned by the entity.
    """
    service = CustomEntitiesService(session, role)
    try:
        entity = await service.reactivate_entity(entity_id)
        return EntityRead.model_validate(entity, from_attributes=True)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.delete("/types/{entity_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_entity(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    entity_id: UUID,
) -> None:
    """Permanently delete an entity and all associated data.

    Warning: This is a hard delete - all data will be permanently lost.
    This includes:
    - All records for the entity
    - All fields owned by the entity
    - All relation links (source or target) involving the entity
    - All relations where this entity is source or target
    """
    service = CustomEntitiesService(session, role)
    try:
        await service.delete_entity(entity_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


# Field Management


@router.post("/{entity_id}/fields", response_model=FieldMetadataRead)
async def create_field(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    entity_id: UUID,
    params: FieldMetadataCreate,
) -> FieldMetadataRead:
    """Create a new field for an entity."""
    service = CustomEntitiesService(session, role)
    try:
        # Pass parameters directly - FieldMetadataCreate will reject relation types
        field = await service.create_field(
            entity_id=entity_id,
            field_key=params.field_key,
            field_type=params.field_type,
            display_name=params.display_name,
            description=params.description,
            enum_options=params.enum_options,
            default_value=params.default_value,
        )
        return FieldMetadataRead.model_validate(field, from_attributes=True)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/{entity_id}/fields", response_model=list[FieldMetadataRead])
async def list_fields(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    entity_id: UUID,
    include_inactive: bool = Query(False, description="Include soft-deleted fields"),
) -> list[FieldMetadataRead]:
    """List fields for an entity."""
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
        field = await service.update_field(
            field_id=field_id,
            display_name=params.display_name,
            description=params.description,
            enum_options=params.enum_options,
            default_value=params.default_value,
        )
        return FieldMetadataRead.model_validate(field, from_attributes=True)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/fields/{field_id}/archive", response_model=FieldMetadataRead)
async def archive_field(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    field_id: UUID,
) -> FieldMetadataRead:
    """Archive (soft delete) field metadata (data preserved)."""
    service = CustomEntitiesService(session, role)
    try:
        field = await service.deactivate_field(field_id)
        return FieldMetadataRead.model_validate(field, from_attributes=True)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/fields/{field_id}/restore", response_model=FieldMetadataRead)
async def restore_field(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    field_id: UUID,
) -> FieldMetadataRead:
    """Restore archived field."""
    service = CustomEntitiesService(session, role)
    try:
        field = await service.reactivate_field(field_id)
        return FieldMetadataRead.model_validate(field, from_attributes=True)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.delete("/fields/{field_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_field(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    field_id: UUID,
) -> None:
    """Permanently delete a field and all associated data.

    Warning: This is a hard delete - all data will be permanently lost.
    """
    service = CustomEntitiesService(session, role)
    try:
        await service.delete_field(field_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


# Relation Endpoints


@router.post("/{entity_id}/relations", response_model=RelationDefinitionRead)
async def create_relation(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    entity_id: UUID,
    params: RelationDefinitionCreate,
) -> RelationDefinitionRead:
    """Create a relation definition for an entity."""
    service = CustomEntitiesService(session, role)
    try:
        relation = await service.create_relation(entity_id=entity_id, data=params)
        return RelationDefinitionRead.model_validate(relation, from_attributes=True)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/{entity_id}/relations", response_model=list[RelationDefinitionRead])
async def list_relations(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    entity_id: UUID,
) -> list[RelationDefinitionRead]:
    """List relation definitions for an entity (as source)."""
    service = CustomEntitiesService(session, role)
    relations = await service.list_relations(entity_id)
    return [
        RelationDefinitionRead.model_validate(r, from_attributes=True)
        for r in relations
    ]


@router.get("/relations", response_model=list[RelationDefinitionRead])
async def list_all_relations(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    source_entity_id: UUID | None = Query(None),
    target_entity_id: UUID | None = Query(None),
    include_inactive: bool = Query(False, description="Include soft-deleted relations"),
) -> list[RelationDefinitionRead]:
    """List relation definitions across the workspace, with optional filters."""
    service = CustomEntitiesService(session, role)
    relations = await service.list_all_relations(
        source_entity_id=source_entity_id,
        target_entity_id=target_entity_id,
        include_inactive=include_inactive,
    )
    return [
        RelationDefinitionRead.model_validate(r, from_attributes=True)
        for r in relations
    ]


@router.post("/relations", response_model=RelationDefinitionRead)
async def create_relation_global(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    params: RelationDefinitionCreateGlobal,
) -> RelationDefinitionRead:
    """Create a relation definition for a specific source entity (global endpoint)."""
    service = CustomEntitiesService(session, role)
    try:
        relation = await service.create_relation(
            entity_id=params.source_entity_id,
            data=RelationDefinitionCreate(
                source_key=params.source_key,
                display_name=params.display_name,
                relation_type=params.relation_type,
                target_entity_id=params.target_entity_id,
            ),
        )
        return RelationDefinitionRead.model_validate(relation, from_attributes=True)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.patch("/relations/{relation_id}", response_model=RelationDefinitionRead)
async def update_relation(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    relation_id: UUID,
    params: RelationDefinitionUpdate,
) -> RelationDefinitionRead:
    service = CustomEntitiesService(session, role)
    try:
        relation = await service.update_relation(relation_id, params)
        return RelationDefinitionRead.model_validate(relation, from_attributes=True)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/relations/{relation_id}/archive", response_model=RelationDefinitionRead)
async def archive_relation(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    relation_id: UUID,
) -> RelationDefinitionRead:
    service = CustomEntitiesService(session, role)
    try:
        relation = await service.deactivate_relation(relation_id)
        return RelationDefinitionRead.model_validate(relation, from_attributes=True)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/relations/{relation_id}/restore", response_model=RelationDefinitionRead)
async def restore_relation(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    relation_id: UUID,
) -> RelationDefinitionRead:
    service = CustomEntitiesService(session, role)
    try:
        relation = await service.reactivate_relation(relation_id)
        return RelationDefinitionRead.model_validate(relation, from_attributes=True)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.delete("/relations/{relation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_relation(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    relation_id: UUID,
) -> None:
    """Permanently delete a relation and all its links."""
    service = CustomEntitiesService(session, role)
    try:
        await service.delete_relation(relation_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


# Data Operations


@router.post("/{entity_id}/records", response_model=RecordRead)
async def create_record(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    entity_id: UUID,
    data: RecordCreate,
) -> RecordRead:
    """Create a new record."""
    service = CustomEntitiesService(session, role)
    try:
        # Extract dynamic fields from request
        record_data = data.model_dump(exclude_unset=True)

        record = await service.create_record(entity_id, record_data)
        return RecordRead.model_validate(record, from_attributes=True)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/records/{record_id}", response_model=RecordRead)
async def get_record(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    record_id: UUID,
) -> RecordRead:
    """Get record by ID."""
    service = CustomEntitiesService(session, role)
    try:
        record = await service.get_record(record_id)
        return RecordRead.model_validate(record, from_attributes=True)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.patch("/records/{record_id}", response_model=RecordRead)
async def update_record(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    record_id: UUID,
    updates: RecordUpdate,
) -> RecordRead:
    """Update record."""
    service = CustomEntitiesService(session, role)
    try:
        # Extract dynamic field updates
        update_data = updates.model_dump(exclude_unset=True)

        record = await service.update_record(record_id, update_data)
        return RecordRead.model_validate(record, from_attributes=True)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.delete("/records/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_record(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    record_id: UUID,
) -> None:
    """Delete record."""
    service = CustomEntitiesService(session, role)
    try:
        await service.delete_record(record_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/records")
async def list_all_records(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    entity_id: UUID | None = Query(None),
    include_deleted: bool = Query(False),
    limit: int = Query(20, ge=1, le=100, description="Maximum items per page"),
    cursor: str | None = Query(None, description="Cursor for pagination"),
    reverse: bool = Query(False, description="Reverse pagination direction"),
) -> CursorPaginatedResponse[RecordRead]:
    """List records globally with cursor-based pagination and optional filters."""
    service = CustomEntitiesService(session, role)
    params = CursorPaginationParams(limit=limit, cursor=cursor, reverse=reverse)
    try:
        result = await service.list_all_records_paginated(
            params, entity_id=entity_id, include_deleted=include_deleted
        )
        return CursorPaginatedResponse(
            items=[
                RecordRead.model_validate(r, from_attributes=True) for r in result.items
            ],
            next_cursor=result.next_cursor,
            prev_cursor=result.prev_cursor,
            has_more=result.has_more,
            has_previous=result.has_previous,
            total_estimate=result.total_estimate,
        )
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/records/{record_id}/archive", response_model=RecordRead)
async def archive_record(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    record_id: UUID,
) -> RecordRead:
    """Soft delete (archive) a record."""
    service = CustomEntitiesService(session, role)
    try:
        record = await service.archive_record(record_id)
        return RecordRead.model_validate(record, from_attributes=True)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/records/{record_id}/restore", response_model=RecordRead)
async def restore_record(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    record_id: UUID,
) -> RecordRead:
    """Restore a soft-deleted record."""
    service = CustomEntitiesService(session, role)
    try:
        record = await service.restore_record(record_id)
        return RecordRead.model_validate(record, from_attributes=True)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/{entity_id}/query", response_model=QueryResponse)
async def query_records(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    entity_id: UUID,
    query: QueryRequest,
) -> QueryResponse:
    """Query records with filters."""
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
                RecordRead.model_validate(r, from_attributes=True) for r in records
            ],
            limit=query.limit,
            offset=query.offset,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


# Schema Inspection


@router.get("/{entity_id}/schema", response_model=EntitySchemaResponse)
async def get_entity_schema(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    entity_id: UUID,
) -> EntitySchemaResponse:
    """Get the dynamic schema for an entity (for UI/validation)."""
    service = CustomEntitiesService(session, role)
    try:
        # Get entity and active fields from service
        schema_result = await service.get_entity_schema(entity_id)

        # Build schema response
        return EntitySchemaResponse(
            entity=EntitySchemaInfo(
                id=str(schema_result.entity.id),
                name=schema_result.entity.name,
                display_name=schema_result.entity.display_name,
                description=schema_result.entity.description,
            ),
            fields=[
                EntitySchemaField(
                    key=f.field_key,
                    type=f.field_type,
                    display_name=f.display_name,
                    description=f.description,
                    enum_options=f.enum_options,
                )
                for f in schema_result.fields
                if f.is_active
            ],
            relations=[
                RelationDefinitionRead.model_validate(r, from_attributes=True)
                for r in schema_result.relations
            ],
        )
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
