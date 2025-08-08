"""Pydantic models for custom entities API."""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from tracecat.entities.types import FieldType


class RelationType(StrEnum):
    """Types of relations between entities."""

    BELONGS_TO = "belongs_to"
    HAS_MANY = "has_many"


# Entity Metadata Models


class EntityMetadataCreate(BaseModel):
    """Request model for creating entity type."""

    name: str = Field(..., min_length=1, max_length=100)
    display_name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1000)
    icon: str | None = Field(default=None, max_length=100)
    settings: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, value: str) -> str:
        """Validate entity name format (same rules as field keys)."""
        if not value:
            raise ValueError("Entity name cannot be empty")

        if len(value) > 100:
            raise ValueError("Entity name cannot exceed 100 characters")

        if not value[0].isalpha():
            raise ValueError("Entity name must start with a letter")

        if not value.replace("_", "").isalnum():
            raise ValueError("Entity name must be alphanumeric with underscores only")

        if value != value.lower():
            raise ValueError("Entity name must be lowercase")

        # Reserved keywords
        reserved = {"id", "created_at", "updated_at", "owner_id", "field_data"}
        if value in reserved:
            raise ValueError(f"Entity name '{value}' is reserved")

        return value


class EntityMetadataUpdate(BaseModel):
    """Request model for updating entity type."""

    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1000)
    icon: str | None = Field(default=None, max_length=100)
    settings: dict[str, Any] | None = None


class EntityMetadataRead(BaseModel):
    """Response model for entity type."""

    id: UUID
    name: str
    display_name: str
    description: str | None
    icon: str | None
    is_active: bool
    settings: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    owner_id: UUID

    model_config = {"from_attributes": True}


# Field Settings Models (for specific field types)


class RelationSettings(BaseModel):
    """Settings for relation fields."""

    relation_type: RelationType
    target_entity_id: UUID
    backref_field_key: str | None = Field(
        default=None,
        description="Field key in target entity for reverse relation",
    )
    cascade_delete: bool = Field(
        default=True,
        description="Delete related records when source is deleted",
    )


class TextFieldSettings(BaseModel):
    """Settings for TEXT field type."""

    min_length: int | None = Field(default=None, ge=0)
    max_length: int | None = Field(default=None, ge=1)
    pattern: str | None = Field(default=None, description="Regex pattern")


class NumberFieldSettings(BaseModel):
    """Settings for INTEGER/NUMBER field types."""

    min: float | None = None
    max: float | None = None


class SelectFieldSettings(BaseModel):
    """Settings for SELECT/MULTI_SELECT field types."""

    options: list[str] = Field(..., min_length=1)


# Field Metadata Models


class FieldMetadataCreate(BaseModel):
    """Request model for creating field."""

    field_key: str = Field(..., min_length=1, max_length=100)
    field_type: FieldType
    display_name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1000)
    field_settings: dict[str, Any] = Field(default_factory=dict)
    relation_settings: RelationSettings | None = Field(
        default=None,
        description="Settings for relation fields (required when field_type is RELATION_*)",
    )

    @field_validator("field_key", mode="before")
    @classmethod
    def validate_field_key(cls, value: str) -> str:
        """Validate field key format.

        Field keys must be:
        - Alphanumeric with underscores only
        - Start with a letter
        - Not exceed 100 characters
        - Lowercase
        - Not use reserved keywords
        """
        if not value:
            raise ValueError("Field key cannot be empty")

        if len(value) > 100:
            raise ValueError("Field key cannot exceed 100 characters")

        if not value[0].isalpha():
            raise ValueError("Field key must start with a letter")

        if not value.replace("_", "").isalnum():
            raise ValueError("Field key must be alphanumeric with underscores only")

        if value != value.lower():
            raise ValueError("Field key must be lowercase")

        # Reserved keywords
        reserved = {"id", "created_at", "updated_at", "owner_id", "field_data"}
        if value in reserved:
            raise ValueError(f"Field key '{value}' is reserved")

        return value


class FieldMetadataUpdate(BaseModel):
    """Request model for updating field display properties."""

    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1000)
    field_settings: dict[str, Any] | None = None


class FieldMetadataRead(BaseModel):
    """Response model for field."""

    id: UUID
    entity_metadata_id: UUID
    field_key: str
    field_type: str
    display_name: str
    description: str | None
    field_settings: dict[str, Any]
    is_active: bool
    is_required: bool
    is_unique: bool
    deactivated_at: datetime | None
    created_at: datetime
    updated_at: datetime
    # Relation fields
    relation_kind: str | None = None
    relation_target_entity_id: UUID | None = None
    relation_backref_field_id: UUID | None = None

    model_config = {"from_attributes": True}


# Entity Data Models


class EntityDataCreate(BaseModel):
    """Request model for creating entity record.

    Note: The actual fields are dynamic based on entity type.
    This is a base model - actual validation happens in service.
    """

    class Config:
        extra = "allow"  # Allow additional fields


class EntityDataUpdate(BaseModel):
    """Request model for updating entity record.

    Note: The actual fields are dynamic based on entity type.
    """

    class Config:
        extra = "allow"  # Allow additional fields


class EntityDataRead(BaseModel):
    """Response model for entity record."""

    id: UUID
    entity_metadata_id: UUID
    field_data: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    owner_id: UUID

    model_config = {"from_attributes": True}


# Query Models


class QueryFilter(BaseModel):
    """Single filter specification."""

    field: str = Field(..., description="Field key to filter on")
    operator: str = Field(
        ...,
        pattern="^(eq|in|ilike|contains|between|is_null|is_not_null)$",
        description="Filter operator",
    )
    value: Any = Field(default=None, description="Filter value(s)")


class QueryRequest(BaseModel):
    """Request model for querying entity records."""

    filters: list[QueryFilter] = Field(default_factory=list)
    limit: int = Field(default=100, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)


class QueryResponse(BaseModel):
    """Response model for query results."""

    records: list[EntityDataRead]
    total: int | None = Field(default=None, description="Total count if available")
    limit: int
    offset: int


# Bulk Operations


class BulkCreateRequest(BaseModel):
    """Request for bulk record creation."""

    records: list[dict[str, Any]] = Field(..., min_length=1, max_length=1000)


class BulkCreateResponse(BaseModel):
    """Response for bulk creation."""

    created: list[UUID]
    failed: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Records that failed validation with error messages",
    )


class ArrayFieldSettings(BaseModel):
    """Settings for ARRAY_* field types."""

    max_items: int | None = Field(default=None, ge=1)


# Relation Operation Models


class RelationOperation(StrEnum):
    """Types of operations on relation fields."""

    ADD = "add"
    REMOVE = "remove"
    REPLACE = "replace"


class HasManyRelationUpdate(BaseModel):
    """Update payload for has_many relation fields."""

    operation: RelationOperation
    target_ids: list[UUID] = Field(
        ...,
        description="UUIDs of target records to add/remove/replace",
        min_length=0,
        max_length=1000,  # Batch size limit
    )


class RelationListRequest(BaseModel):
    """Request for listing related records."""

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=100)
    filters: list["QueryFilter"] | None = Field(
        default=None,
        description="Optional filters on target records",
    )


class RelationListResponse(BaseModel):
    """Response for related records listing."""

    records: list[EntityDataRead]
    total: int
    page: int
    page_size: int
    has_next: bool
