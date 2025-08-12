"""Pydantic models for custom entities API."""

from datetime import datetime
from enum import StrEnum
from typing import Any, Self
from uuid import UUID

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

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
    is_required: bool = Field(
        default=False,
        description="Field must have a non-null value (or non-empty for has_many relations)",
    )
    is_unique: bool = Field(
        default=False,
        description="Field value must be unique across all records (one-to-one for belongs_to)",
    )
    default_value: Any | None = Field(
        default=None,
        description="Default value for the field (only for primitive types)",
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

    @field_validator("is_unique", mode="after")
    @classmethod
    def validate_unique_for_field_type(cls, v: bool, info: ValidationInfo) -> bool:
        """Validate unique constraint is appropriate for field type."""
        if not v:
            return v

        field_type = info.data.get("field_type")

        # For now, only support unique on scalar types and belongs_to
        allowed_types = {
            FieldType.TEXT,
            FieldType.INTEGER,
            FieldType.NUMBER,
            FieldType.DATE,
            FieldType.DATETIME,
            FieldType.SELECT,
            FieldType.RELATION_BELONGS_TO,
        }

        if field_type not in allowed_types:
            raise ValueError(
                f"Unique constraint not supported for field type {field_type}"
            )

        return v

    @model_validator(mode="after")
    def validate_default_value(self) -> Self:
        """Validate default value is appropriate for field type."""
        if self.default_value is None:
            return self

        # Only allow defaults for primitive field types
        primitive_types = {
            FieldType.TEXT,
            FieldType.INTEGER,
            FieldType.NUMBER,
            FieldType.BOOL,
            FieldType.SELECT,
            FieldType.MULTI_SELECT,
        }

        if self.field_type not in primitive_types:
            raise ValueError(
                f"Default values not supported for field type {self.field_type}. "
                f"Only primitive types support defaults: {', '.join(t.value for t in primitive_types)}"
            )

        # Type-specific validation
        if self.field_type == FieldType.TEXT:
            if not isinstance(self.default_value, str):
                raise ValueError(
                    f"Default value for TEXT field must be a string, got {type(self.default_value).__name__}"
                )
        elif self.field_type == FieldType.INTEGER:
            if not isinstance(self.default_value, int) or isinstance(
                self.default_value, bool
            ):
                raise ValueError(
                    f"Default value for INTEGER field must be an integer, got {type(self.default_value).__name__}"
                )
        elif self.field_type == FieldType.NUMBER:
            if not isinstance(self.default_value, int | float) or isinstance(
                self.default_value, bool
            ):
                raise ValueError(
                    f"Default value for NUMBER field must be a number, got {type(self.default_value).__name__}"
                )
        elif self.field_type == FieldType.BOOL:
            if not isinstance(self.default_value, bool):
                raise ValueError(
                    f"Default value for BOOL field must be a boolean, got {type(self.default_value).__name__}"
                )
        elif self.field_type == FieldType.SELECT:
            if not isinstance(self.default_value, str):
                raise ValueError(
                    f"Default value for SELECT field must be a string, got {type(self.default_value).__name__}"
                )
            # Validate against options if field_settings provided
            if self.field_settings and "options" in self.field_settings:
                if self.default_value not in self.field_settings["options"]:
                    raise ValueError(
                        f"Default value '{self.default_value}' not in available options: {self.field_settings['options']}"
                    )
        elif self.field_type == FieldType.MULTI_SELECT:
            if not isinstance(self.default_value, list):
                raise ValueError(
                    f"Default value for MULTI_SELECT field must be a list, got {type(self.default_value).__name__}"
                )
            if not all(isinstance(item, str) for item in self.default_value):
                raise ValueError(
                    "All items in MULTI_SELECT default value must be strings"
                )
            # Validate against options if field_settings provided
            if self.field_settings and "options" in self.field_settings:
                invalid_options = [
                    opt
                    for opt in self.default_value
                    if opt not in self.field_settings["options"]
                ]
                if invalid_options:
                    raise ValueError(
                        f"Default values {invalid_options} not in available options: {self.field_settings['options']}"
                    )

        return self


class FieldMetadataUpdate(BaseModel):
    """Request model for updating field display properties."""

    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1000)
    field_settings: dict[str, Any] | None = None
    is_required: bool | None = None
    is_unique: bool | None = None
    default_value: Any | None = None


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
    default_value: Any | None = None

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
