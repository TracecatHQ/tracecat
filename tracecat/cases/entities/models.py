from typing import Any

from pydantic import UUID4, BaseModel, Field


class CaseEntityLinkCreate(BaseModel):
    """Create a link between a case and an entity record."""

    entity_metadata_id: UUID4 = Field(description="Entity type ID")
    entity_data_id: UUID4 | None = Field(
        None, description="Existing entity record ID to link"
    )
    entity_data: dict[str, Any] | None = Field(
        None, description="Data for creating a new entity record"
    )


class CaseEntityLinkRead(BaseModel):
    """Case entity link with entity details."""

    id: UUID4
    case_id: UUID4
    entity_metadata_id: UUID4
    entity_data_id: UUID4
    entity_metadata: "EntityMetadataRead | None" = None
    entity_data: "EntityDataRead | None" = None

    model_config = {"from_attributes": True}


class EntityMetadataRead(BaseModel):
    """Entity type metadata."""

    id: UUID4
    name: str
    description: str | None
    is_active: bool

    model_config = {"from_attributes": True}


class EntityDataRead(BaseModel):
    """Entity record data."""

    id: UUID4
    entity_metadata_id: UUID4
    field_data: dict[str, Any]

    model_config = {"from_attributes": True}


class EntityTypeListRead(BaseModel):
    """Available entity type for selection."""

    id: UUID4
    name: str
    description: str | None

    model_config = {"from_attributes": True}
