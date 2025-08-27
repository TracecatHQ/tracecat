from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Self, cast

from pydantic import BaseModel, Field, field_validator, model_validator
from slugify import slugify

from tracecat.entities.enums import FieldType


class EntityCreate(BaseModel):
    key: str = Field(..., min_length=1, description="Immutable entity key (snake_case)")
    display_name: str
    description: str | None = None
    icon: str | None = None

    @field_validator("key", mode="before")
    @classmethod
    def normalize_key(cls, v: str) -> str:
        return slugify(str(v), separator="_")


class EntityUpdate(BaseModel):
    display_name: str | None = None
    description: str | None = None
    icon: str | None = None


class EntityFieldOptionCreate(BaseModel):
    label: str = Field(..., min_length=1)
    key: str = Field(..., min_length=1, description="Normalized option key")

    @model_validator(mode="before")
    @classmethod
    def ensure_key(cls, data: Any):
        # If key is not provided, derive from label before standard field validators
        if isinstance(data, dict):
            if not data.get("key") and "label" in data:
                data = {**data, "key": slugify(str(data["label"]), separator="_")}
        return data

    @field_validator("key", mode="before")
    @classmethod
    def normalize_key(cls, v: str) -> str:
        return slugify(str(v), separator="_")


class EntityFieldCreate(BaseModel):
    key: str = Field(..., min_length=1, description="Immutable field key (snake_case)")
    type: FieldType
    display_name: str
    description: str | None = None
    default_value: Any | None = None
    options: list[EntityFieldOptionCreate] | None = None

    @field_validator("key", mode="before")
    @classmethod
    def normalize_key(cls, v: str) -> str:
        return slugify(str(v), separator="_")

    @model_validator(mode="after")
    def validate_and_coerce_default_and_options(self) -> Self:
        # Validate options usage by type
        if self.options is not None and self.type not in (
            FieldType.SELECT,
            FieldType.MULTI_SELECT,
        ):
            raise ValueError(
                "Options are only allowed for SELECT or MULTI_SELECT types"
            )

        # Validate unique option keys
        if self.options:
            keys = [opt.key for opt in self.options]
            if len(keys) != len(set(keys)):
                raise ValueError("Duplicate option key(s) found in options list")

        # Coerce/validate default_value according to type
        if self.default_value is not None:
            self.default_value = coerce_default_value(self.type, self.default_value)

            # For selects, we coerce to string(s) above; optionally ensure default within options
            # Enforce membership in provided options for SELECT/MULTI_SELECT
            if self.type in (FieldType.SELECT, FieldType.MULTI_SELECT):
                if not self.options:
                    raise ValueError(
                        "Options must be provided when setting a default for SELECT/MULTI_SELECT"
                    )
                opt_keys = {opt.key for opt in self.options}
                if self.type == FieldType.SELECT:
                    if self.default_value not in opt_keys:
                        raise ValueError(
                            "Default value must match one of the option keys"
                        )
                else:  # MULTI_SELECT
                    invalid = [
                        v for v in cast(list, self.default_value) if v not in opt_keys
                    ]
                    if invalid:
                        raise ValueError(
                            f"Default values not in options: {', '.join(invalid)}"
                        )

        return self


class EntityFieldUpdate(BaseModel):
    display_name: str | None = None
    description: str | None = None
    # Explicitly allow setting default_value to null
    default_value: Any | None = Field(default=None)
    # Full replacement list for enum options (optional)
    options: list[EntityFieldOptionCreate] | None = None

    @model_validator(mode="after")
    def validate_options_uniqueness(self) -> Self:
        if self.options:
            keys = [opt.key for opt in self.options]
            if len(keys) != len(set(keys)):
                raise ValueError("Duplicate option key(s) found in options list")
        return self


def coerce_default_value(field_type: FieldType, value: Any) -> Any:
    """Coerce and validate a default value for a given field type.

    Matches the behavior used elsewhere while leveraging Pydantic invocation points.
    """
    if value is None:
        return None

    match field_type:
        case FieldType.INTEGER:
            return int(value)
        case FieldType.NUMBER:
            return float(value)
        case FieldType.TEXT:
            return str(value)
        case FieldType.BOOL:
            # Handle string representations of boolean values
            if isinstance(value, str):
                value_lower = value.lower()
                if value_lower in ("true", "1", "yes", "on"):
                    return True
                elif value_lower in ("false", "0", "no", "off", ""):
                    return False
                else:
                    # For other strings, use Python's bool() which returns False only for empty strings
                    return bool(value)
            return bool(value)
        case FieldType.JSON:
            if not isinstance(value, dict | list):
                raise ValueError(
                    f"JSON field requires dict or list, got {type(value).__name__}"
                )
            return value
        case FieldType.DATETIME:
            # Store as ISO string
            if isinstance(value, datetime):
                return value.isoformat()
            try:
                return datetime.fromisoformat(str(value)).isoformat()
            except Exception as e:  # noqa: BLE001
                raise ValueError(f"Cannot convert {value!r} to datetime: {e}") from e
        case FieldType.DATE:
            # Coerce to date-only ISO string
            if isinstance(value, datetime):
                return value.date().isoformat()
            try:
                dt = datetime.fromisoformat(str(value))
                return dt.date().isoformat()
            except Exception as e:  # noqa: BLE001
                raise ValueError(f"Cannot convert {value!r} to date: {e}") from e
        case FieldType.SELECT:
            return str(value)
        case FieldType.MULTI_SELECT:
            if not isinstance(value, list):
                raise ValueError(
                    f"MULTI_SELECT field requires list, got {type(value).__name__}"
                )
            return [str(v) for v in value]
    return value


class EntityFieldOptionRead(BaseModel):
    id: uuid.UUID
    field_id: uuid.UUID
    key: str
    label: str
    description: str | None = None
    created_at: datetime
    updated_at: datetime


class EntityFieldRead(BaseModel):
    id: uuid.UUID
    entity_id: uuid.UUID
    key: str
    type: FieldType
    display_name: str
    description: str | None = None
    is_active: bool
    default_value: Any | None = None
    created_at: datetime
    updated_at: datetime
    options: list[EntityFieldOptionRead] = Field(default_factory=list)


class EntityRead(BaseModel):
    id: uuid.UUID
    key: str
    display_name: str
    description: str | None = None
    icon: str | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime
