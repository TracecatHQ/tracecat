from typing import Any

from pydantic import UUID4, BaseModel, Field


class CaseRecordLinkCreate(BaseModel):
    """Create a link between a case and a record."""

    entity_id: UUID4 = Field(description="Entity ID")
    record_id: UUID4 | None = Field(None, description="Existing record ID to link")
    record_data: dict[str, Any] | None = Field(
        None, description="Data for creating a new record"
    )


class CaseRecordLinkRead(BaseModel):
    """Case record link with entity and record details."""

    id: UUID4
    case_id: UUID4
    entity_id: UUID4
    record_id: UUID4
    entity: "EntityRead | None" = None
    record: "RecordRead | None" = None

    model_config = {"from_attributes": True}


class EntityRead(BaseModel):
    """Entity metadata."""

    id: UUID4
    name: str
    display_name: str
    description: str | None
    icon: str | None
    is_active: bool

    model_config = {"from_attributes": True}


class RecordRead(BaseModel):
    """Record data."""

    id: UUID4
    entity_id: UUID4
    field_data: dict[str, Any]

    model_config = {"from_attributes": True}


class EntityListRead(BaseModel):
    """Available entity for selection."""

    id: UUID4
    name: str
    description: str | None

    model_config = {"from_attributes": True}
