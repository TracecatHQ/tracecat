"""Pydantic models for custom entities API."""

from datetime import datetime
from typing import Any, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

from tracecat.db.schemas import Entity, FieldMetadata
from tracecat.entities.enums import RelationType
from tracecat.entities.types import FieldType, validate_field_key_format
from tracecat.entities.validation import (
    validate_default_value_type,
    validate_enum_options,
)

# Entity Models


class EntityCreate(BaseModel):
    """Request model for creating entity."""

    name: str = Field(..., min_length=1, max_length=100)
    display_name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1000)
    icon: str | None = Field(default=None, max_length=100)

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, value: str) -> str:
        """Validate entity name format (same rules as field keys)."""
        return validate_field_key_format(value)


class EntityUpdate(BaseModel):
    """Request model for updating entity."""

    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1000)
    icon: str | None = Field(default=None, max_length=100)


class EntityRead(BaseModel):
    """Response model for entity."""

    id: UUID
    name: str
    display_name: str
    description: str | None
    icon: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    owner_id: UUID

    model_config = {"from_attributes": True}


# Field Settings Models (for specific field types)


# Field Metadata Models


class FieldMetadataCreate(BaseModel):
    """Request model for creating field."""

    field_key: str = Field(..., min_length=1, max_length=100)
    field_type: FieldType
    display_name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1000)
    enum_options: list[str] | None = Field(
        default=None,
        min_length=1,
        description="Options for SELECT/MULTI_SELECT fields",
    )
    default_value: Any | None = Field(
        default=None,
        description="Default value for the field (only for primitive types)",
    )

    @field_validator("field_key", mode="before")
    @classmethod
    def validate_field_key(cls, value: str) -> str:
        """Validate field key format."""
        return validate_field_key_format(value)

    @model_validator(mode="after")
    def validate_enum_options(self) -> Self:
        """Validate enum_options for SELECT/MULTI_SELECT fields."""
        # SELECT and MULTI_SELECT require enum_options
        if self.field_type in (FieldType.SELECT, FieldType.MULTI_SELECT):
            if not self.enum_options:
                raise PydanticCustomError(
                    "missing_enum_options",
                    "Field type '{field_type}' requires enum_options",
                    {"field_type": self.field_type.value},
                )
            # Use shared validator
            self.enum_options = validate_enum_options(self.enum_options)
        # Non-SELECT fields should not have enum_options
        elif self.enum_options:
            raise PydanticCustomError(
                "invalid_enum_options",
                "Field type '{field_type}' cannot have enum_options",
                {"field_type": self.field_type.value},
            )

        return self

    @model_validator(mode="after")
    def validate_default_value(self) -> Self:
        """Validate default value is appropriate for field type."""
        if self.default_value is None:
            return self

        # Use shared validator for consistent validation
        try:
            self.default_value = validate_default_value_type(
                self.default_value, self.field_type, self.enum_options
            )
        except PydanticCustomError:
            # Re-raise as-is for consistent error messages
            raise

        return self


class FieldMetadataUpdate(BaseModel):
    """Request model for updating field display properties."""

    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1000)
    enum_options: list[str] | None = Field(
        default=None,
        min_length=1,
        description="Options for SELECT/MULTI_SELECT fields",
    )
    default_value: Any | None = None

    @field_validator("enum_options", mode="after")
    @classmethod
    def validate_enum_options(cls, v: list[str] | None) -> list[str] | None:
        """Validate enum options are unique and non-empty."""
        # Use shared validator
        return validate_enum_options(v)


class FieldMetadataRead(BaseModel):
    """Response model for field."""

    id: UUID
    entity_id: UUID
    field_key: str
    field_type: str
    display_name: str
    description: str | None
    is_active: bool
    deactivated_at: datetime | None
    created_at: datetime
    updated_at: datetime
    # Enum field options
    enum_options: list[str] | None = None
    default_value: Any | None = None

    model_config = {"from_attributes": True}


# Record Models


class RecordCreate(BaseModel):
    """Request model for creating record.

    Note: The actual fields are dynamic based on entity type.
    This is a base model - actual validation happens in service.
    """

    model_config = ConfigDict(extra="allow")  # Allow additional fields


class RecordUpdate(BaseModel):
    """Request model for updating record.

    Note: The actual fields are dynamic based on entity type.
    """

    model_config = ConfigDict(extra="allow")  # Allow additional fields


class RecordRead(BaseModel):
    """Response model for record."""

    id: UUID
    entity_id: UUID
    field_data: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
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

    records: list[RecordRead]
    total: int | None = Field(default=None, description="Total count if available")
    limit: int
    offset: int


class RecordsListResponse(BaseModel):
    """Response model for listing records globally."""

    records: list[RecordRead]
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


# Additional Response Models


class EntitySchemaField(BaseModel):
    """Schema field description for API responses."""

    key: str = Field(..., description="Field key")
    type: str = Field(..., description="Field type")
    display_name: str = Field(..., description="Display name")
    description: str | None = Field(default=None, description="Field description")
    enum_options: list[str] | None = Field(
        default=None, description="Options for SELECT/MULTI_SELECT fields"
    )


class EntitySchemaInfo(BaseModel):
    """Entity metadata in schema response."""

    id: str = Field(..., description="Entity ID")
    name: str = Field(..., description="Entity name")
    display_name: str = Field(..., description="Display name")
    description: str | None = Field(default=None, description="Entity description")


class EntitySchemaResponse(BaseModel):
    """Response for entity schema endpoint."""

    entity: EntitySchemaInfo = Field(..., description="Entity metadata")
    fields: list[EntitySchemaField] = Field(..., description="Field definitions")
    relations: list["RelationDefinitionRead"] = Field(
        default_factory=list, description="Relation definitions"
    )


class EntitySchemaResult(BaseModel):
    """Response model for entity schema with full metadata."""

    entity: "Entity" = Field(..., description="Full Entity object")
    fields: list["FieldMetadata"] = Field(
        ..., description="List of FieldMetadata objects"
    )
    relations: list[Any] = Field(
        default_factory=list, description="List of RelationDefinition objects"
    )

    model_config = ConfigDict(arbitrary_types_allowed=True)


# Relations API models


class RelationDefinitionCreate(BaseModel):
    source_key: str = Field(..., min_length=1, max_length=100)
    display_name: str = Field(..., min_length=1, max_length=255)
    relation_type: RelationType
    target_entity_id: UUID

    @field_validator("source_key", mode="before")
    @classmethod
    def validate_source_key(cls, value: str) -> str:
        return validate_field_key_format(value)


class RelationDefinitionCreateGlobal(BaseModel):
    """Global create model that includes the source entity ID."""

    source_entity_id: UUID
    source_key: str = Field(..., min_length=1, max_length=100)
    display_name: str = Field(..., min_length=1, max_length=255)
    relation_type: RelationType
    target_entity_id: UUID

    @field_validator("source_key", mode="before")
    @classmethod
    def validate_source_key(cls, value: str) -> str:
        return validate_field_key_format(value)


class RelationDefinitionUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    source_key: str | None = Field(default=None, min_length=1, max_length=100)

    @field_validator("source_key", mode="before")
    @classmethod
    def validate_source_key(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return validate_field_key_format(value)


class RelationDefinitionRead(BaseModel):
    id: UUID
    owner_id: UUID
    source_entity_id: UUID
    target_entity_id: UUID
    source_key: str
    display_name: str
    relation_type: RelationType
    is_active: bool
    deactivated_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
