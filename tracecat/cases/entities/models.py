from datetime import datetime
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
    created_at: datetime | None = None
    updated_at: datetime | None = None
    entity: "CaseEntityRead | None" = None
    record: "CaseRecordRead | None" = None

    model_config = {"from_attributes": True}


class CaseEntityRead(BaseModel):
    """Entity metadata."""

    id: UUID4
    name: str
    display_name: str
    description: str | None
    icon: str | None
    is_active: bool

    model_config = {"from_attributes": True}


class CaseRecordRead(BaseModel):
    """Record data."""

    id: UUID4
    entity_id: UUID4
    field_data: dict[str, Any]
    relation_fields: list[str] = Field(
        default_factory=list,
        description="List of field keys that are relations (ONE_TO_ONE or ONE_TO_MANY)",
    )

    model_config = {"from_attributes": True}


class CaseEntityListRead(BaseModel):
    """Available entity for selection."""

    id: UUID4
    name: str
    description: str | None

    model_config = {"from_attributes": True}
