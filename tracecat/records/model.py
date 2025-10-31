from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import orjson
from pydantic import BaseModel, Field, field_validator

MAX_BYTES = 200 * 1024  # 200 KB per-field


class RecordCreate(BaseModel):
    """Create payload for an entity record.

    Data is a free-form JSON object whose keys correspond to entity field keys.
    Values are validated and coerced by the service using the entity's schema.
    """

    data: dict[str, Any] = Field(default_factory=dict)

    @field_validator("data")
    @classmethod
    def enforce_value_sizes(cls, v: dict[str, Any]) -> dict[str, Any]:
        """Basic size guard on individual values to avoid excessively large payloads."""
        for key, value in v.items():
            if len(orjson.dumps(value)) > MAX_BYTES:
                raise ValueError(f"Field '{key}' value must be less than 200 KB")
        return v


class RecordUpdate(BaseModel):
    """Partial update for a record's data map.

    Any keys provided will be merged into the existing record data after
    validation/coercion. Keys not present remain unchanged.
    """

    data: dict[str, Any] = Field(default_factory=dict)

    @field_validator("data")
    @classmethod
    def enforce_value_sizes(cls, v: dict[str, Any]) -> dict[str, Any]:
        for key, value in v.items():
            if len(orjson.dumps(value)) > MAX_BYTES:
                raise ValueError(f"Field '{key}' value must be less than 200 KB")
        return v


class RecordRead(BaseModel):
    id: uuid.UUID
    entity_id: uuid.UUID
    data: dict[str, Any]
    created_at: datetime
    updated_at: datetime
