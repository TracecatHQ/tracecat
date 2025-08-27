from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Self, cast

import orjson
from pydantic import BaseModel, Field, field_validator, model_validator
from slugify import slugify

from tracecat.entities.enums import FieldType

MAX_BYTES = 200 * 1024  # 200 KB


class EntityCreate(BaseModel):
    key: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Immutable entity key (snake_case)",
    )
    display_name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1000)
    icon: str | None = Field(default=None, max_length=255)

    @field_validator("key", mode="before")
    @classmethod
    def normalize_key(cls, v: str) -> str:
        return slugify(str(v), separator="_")


class EntityUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=1000)
    icon: str | None = Field(default=None, max_length=255)


class EntityFieldOptionCreate(BaseModel):
    key: str | None = Field(default=None, max_length=255)
    label: str = Field(..., min_length=1, max_length=255)

    @model_validator(mode="before")
    @classmethod
    def set_key_from_label(cls, values: dict[str, Any]) -> dict[str, Any]:
        # If key is None, empty, or not provided, use slugified label
        if not values.get("key") and "label" in values:
            values["key"] = slugify(str(values["label"]), separator="_")
        return values

    @field_validator("key", mode="after")
    @classmethod
    def ensure_key_is_set(cls, v: str | None, info) -> str:
        # After model validation, ensure key is always a string
        if v is None or v == "":
            # This shouldn't happen if model_validator worked, but as a safety net
            if "label" in info.data:
                return slugify(str(info.data["label"]), separator="_")
            raise ValueError("Key cannot be None when label is not provided")
        return v

    @property
    def resolved_key(self) -> str:
        """Get the key, guaranteed to be a non-empty string after validation."""
        # Type assertion - we know it's a string after validation
        if self.key is None:
            raise ValueError("Field key cannot be empty")
        return self.key


class EntityFieldCreate(BaseModel):
    key: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Immutable field key (snake_case)",
    )
    type: FieldType
    display_name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1000)
    # Allow any JSON-serializable scalar/collection or null; coercion runs in validator
    default_value: Any | None = Field(
        default=None, description="Default value for the field"
    )
    options: list[EntityFieldOptionCreate] | None = None

    @field_validator("default_value", mode="before")
    @classmethod
    def check_default_value_size(cls, v: Any) -> Any:
        # Ensure we always return the input value so later validators see it
        if len(orjson.dumps(v)) > MAX_BYTES:
            raise ValueError("Default value must be less than 200 KB")
        return v

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

        # Validate unique option keys (after normalization/autogeneration)
        if self.options:
            keys = [opt.resolved_key for opt in self.options]
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
                # Ensure keys are normalized/generated using the option model logic
                opt_keys = {opt.resolved_key for opt in self.options}
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
    display_name: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=1000)
    # Explicitly allow setting default_value to null
    default_value: Any | None = Field(
        default=None, description="Default value for the field"
    )
    # Full replacement list for enum options (optional)
    options: list[EntityFieldOptionCreate] | None = None

    @field_validator("default_value", mode="before")
    @classmethod
    def check_default_value_size(cls, v: Any) -> Any:
        if len(orjson.dumps(v)) > MAX_BYTES:
            raise ValueError("Default value must be less than 200 KB")
        return v

    @model_validator(mode="after")
    def validate_options_uniqueness(self) -> Self:
        if self.options:
            keys = [opt.resolved_key for opt in self.options]
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
            # Accept only mapping or sequence (list) types for JSON defaults
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
