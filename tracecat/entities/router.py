"""API router for custom entities."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ValidationError

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
    HasManyRelationUpdate,
    QueryRequest,
    QueryResponse,
    RelationListRequest,
    RelationListResponse,
)
from tracecat.entities.service import CustomEntitiesService
from tracecat.entities.types import FieldType
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


@router.post("/types/{entity_id}/reactivate")
async def reactivate_entity_type(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    entity_id: UUID,
) -> dict:
    """Reactivate soft-deleted entity type."""
    service = CustomEntitiesService(session, role)
    try:
        entity = await service.get_entity_type(entity_id)
        if entity.is_active:
            raise HTTPException(status_code=400, detail="Entity type is already active")
        entity.is_active = True
        await service.session.commit()
        return {"message": f"Entity type {entity_id} reactivated"}
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
            is_required=params.is_required,
            is_unique=params.is_unique,
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
            is_required=params.is_required,
            is_unique=params.is_unique,
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


# Relation Field Endpoints


@router.post("/types/{entity_id}/fields/relation", response_model=FieldMetadataRead)
async def create_relation_field(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    entity_id: UUID,
    params: FieldMetadataCreate,
) -> FieldMetadataRead:
    """Create a relation field.

    Requires relation_settings in the request body.
    """
    if not params.relation_settings:
        raise HTTPException(
            status_code=400, detail="relation_settings required for relation fields"
        )

    service = CustomEntitiesService(session, role)
    try:
        field = await service.create_relation_field(
            entity_id=entity_id,
            field_key=params.field_key,
            field_type=params.field_type,
            display_name=params.display_name,
            relation_settings=params.relation_settings,
            description=params.description,
            is_required=params.is_required,
            is_unique=params.is_unique,
        )
        return FieldMetadataRead.model_validate(field, from_attributes=True)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


class PairedRelationCreate(BaseModel):
    """Request for creating paired relation fields."""

    source_entity_id: UUID
    source_field_key: str
    source_display_name: str
    target_entity_id: UUID
    target_field_key: str
    target_display_name: str
    cascade_delete: bool = True


@router.post("/types/fields/paired-relation", response_model=list[FieldMetadataRead])
async def create_paired_relation_fields(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    params: PairedRelationCreate,
) -> list[FieldMetadataRead]:
    """Atomically create bidirectional relation fields."""
    service = CustomEntitiesService(session, role)
    try:
        belongs_to_field, has_many_field = await service.create_paired_relation_fields(
            source_entity_id=params.source_entity_id,
            source_field_key=params.source_field_key,
            source_display_name=params.source_display_name,
            target_entity_id=params.target_entity_id,
            target_field_key=params.target_field_key,
            target_display_name=params.target_display_name,
            cascade_delete=params.cascade_delete,
        )
        return [
            FieldMetadataRead.model_validate(belongs_to_field, from_attributes=True),
            FieldMetadataRead.model_validate(has_many_field, from_attributes=True),
        ]
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


# Record Relation Endpoints


@router.put("/records/{record_id}/relations/{field_key}")
async def update_record_relation(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    record_id: UUID,
    field_key: str,
    value: UUID | None | HasManyRelationUpdate,
) -> dict[str, Any]:
    """Update a relation field value.

    For belongs_to: Accept UUID or null
    For has_many: Accept HasManyRelationUpdate
    """
    service = CustomEntitiesService(session, role)

    try:
        # Get the record and field metadata
        record = await service.get_record(record_id)

        # Find the field
        fields = await service.list_fields(record.entity_metadata_id)
        field = next((f for f in fields if f.field_key == field_key), None)

        if not field:
            raise HTTPException(
                status_code=404, detail=f"Field '{field_key}' not found"
            )

        # Check if it's a relation field
        if field.field_type not in (
            FieldType.RELATION_BELONGS_TO,
            FieldType.RELATION_HAS_MANY,
        ):
            raise HTTPException(
                status_code=400, detail=f"Field '{field_key}' is not a relation field"
            )

        # Handle based on field type
        if field.field_type == FieldType.RELATION_BELONGS_TO:
            # Validate value is UUID or None
            if value is not None and not isinstance(value, UUID):
                raise HTTPException(
                    status_code=400, detail="Belongs-to relation expects UUID or null"
                )

            await service.update_belongs_to_relation(
                source_record_id=record_id,
                field=field,
                target_record_id=value if isinstance(value, UUID) else None,
            )

            return {
                "message": "Relation updated",
                "target_id": str(value) if value else None,
            }

        else:  # RELATION_HAS_MANY
            # Validate value is HasManyRelationUpdate
            if not isinstance(value, HasManyRelationUpdate):
                raise HTTPException(
                    status_code=400,
                    detail="Has-many relation expects operation payload",
                )

            stats = await service.update_has_many_relation(
                source_record_id=record_id,
                field=field,
                operation=value,
            )

            return {"message": "Relation updated", "stats": stats}

    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get(
    "/records/{record_id}/relations/{field_key}", response_model=RelationListResponse
)
async def list_related_records(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    record_id: UUID,
    field_key: str,
    request: RelationListRequest = RelationListRequest(),
) -> RelationListResponse:
    """List related records with pagination.

    REQUIRED: Pagination parameters (page, page_size)
    Max page_size: 100
    """
    service = CustomEntitiesService(session, role)

    try:
        # Get the record and field metadata
        record = await service.get_record(record_id)

        # Find the field
        fields = await service.list_fields(record.entity_metadata_id)
        field = next((f for f in fields if f.field_key == field_key), None)

        if not field:
            raise HTTPException(
                status_code=404, detail=f"Field '{field_key}' not found"
            )

        # Check if it's a relation field
        if field.field_type not in (
            FieldType.RELATION_BELONGS_TO,
            FieldType.RELATION_HAS_MANY,
        ):
            raise HTTPException(
                status_code=400, detail=f"Field '{field_key}' is not a relation field"
            )

        # Query related records
        filters = [f.model_dump() for f in request.filters] if request.filters else None

        records, total = await service.query_builder.has_related(
            source_record_id=record_id,
            field_id=field.id,
            target_filters=filters,
            page=request.page,
            page_size=request.page_size,
        )

        has_next = (request.page * request.page_size) < total

        return RelationListResponse(
            records=[
                EntityDataRead.model_validate(r, from_attributes=True) for r in records
            ],
            total=total,
            page=request.page,
            page_size=request.page_size,
            has_next=has_next,
        )

    except TracecatNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


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
