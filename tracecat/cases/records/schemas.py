"""Models for case records."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

import orjson
from pydantic import BaseModel, Field, field_validator

from tracecat import config
from tracecat.records.model import MAX_BYTES


class CaseRecordCreate(BaseModel):
    """Model for creating a new entity record and linking it to a case."""

    entity_key: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Key of the entity type",
    )
    data: dict[str, Any] = Field(
        default_factory=dict,
        description="Entity record data",
    )

    @field_validator("data")
    @classmethod
    def enforce_value_sizes(cls, v: dict[str, Any]) -> dict[str, Any]:
        """Match record model: limit each field's serialized size to 200KB."""
        for key, value in v.items():
            if len(orjson.dumps(value)) > MAX_BYTES:
                raise ValueError(f"Field '{key}' value must be less than 200 KB")
        return v


class CaseRecordLink(BaseModel):
    """Model for linking an existing entity record to a case."""

    entity_record_id: uuid.UUID = Field(
        ...,
        description="ID of the existing entity record to link",
    )


class CaseRecordUpdate(BaseModel):
    """Model for updating a case record's entity data."""

    data: dict[str, Any] = Field(
        ...,
        description="Updated entity record data",
    )

    @field_validator("data")
    @classmethod
    def enforce_value_sizes(cls, v: dict[str, Any]) -> dict[str, Any]:
        for key, value in v.items():
            if len(orjson.dumps(value)) > MAX_BYTES:
                raise ValueError(f"Field '{key}' value must be less than 200 KB")
        return v


class CaseRecordRead(BaseModel):
    """Model for reading a case record with full details."""

    id: uuid.UUID = Field(..., description="Case record link ID")
    case_id: uuid.UUID = Field(..., description="Case ID")
    entity_id: uuid.UUID = Field(..., description="Entity type ID")
    record_id: uuid.UUID = Field(..., description="Entity record ID")
    entity_key: str = Field(..., description="Entity type key")
    entity_display_name: str = Field(..., description="Entity display name")
    data: dict[str, Any] = Field(..., description="Entity record data")
    created_at: datetime
    updated_at: datetime


class CaseRecordListResponse(BaseModel):
    """Response model for listing case records."""

    items: list[CaseRecordRead] = Field(
        default_factory=list,
        description="List of case records",
    )
    total: int = Field(
        ...,
        description="Total number of records",
        ge=0,
        le=config.TRACECAT__MAX_RECORDS_PER_CASE,
    )


class CaseRecordDeleteResponse(BaseModel):
    """Response model for unlinking a case record."""

    action: Literal["unlink", "delete"] = Field(
        ...,
        description="Action (unlink or delete)",
    )
    case_id: uuid.UUID = Field(
        ...,
        description="Case ID",
    )
    record_id: uuid.UUID = Field(
        ...,
        description="Record ID",
    )
    case_record_id: uuid.UUID = Field(
        ...,
        description="Case record ID",
    )
