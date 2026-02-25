from __future__ import annotations

import uuid

from pydantic import Field, model_validator

from tracecat.core.schemas import Schema

# --- Option Schemas ---


class CaseDropdownOptionCreate(Schema):
    """Create a new option within a dropdown definition."""

    label: str = Field(..., min_length=1, max_length=255)
    ref: str = Field(..., min_length=1, max_length=255)
    icon_name: str | None = Field(default=None, max_length=100)
    color: str | None = Field(default=None, max_length=50)
    position: int = Field(default=0)


class CaseDropdownOptionUpdate(Schema):
    """Update an existing dropdown option."""

    label: str | None = Field(default=None, min_length=1, max_length=255)
    ref: str | None = Field(default=None, min_length=1, max_length=255)
    icon_name: str | None = Field(default=None, max_length=100)
    color: str | None = Field(default=None, max_length=50)
    position: int | None = Field(default=None)


class CaseDropdownOptionRead(Schema):
    """Read model for a dropdown option."""

    id: uuid.UUID
    label: str
    ref: str
    icon_name: str | None = None
    color: str | None = None
    position: int


# --- Definition Schemas ---


class CaseDropdownDefinitionCreate(Schema):
    """Create a new dropdown definition with initial options."""

    name: str = Field(..., min_length=1, max_length=255)
    ref: str = Field(..., min_length=1, max_length=255)
    icon_name: str | None = Field(default=None, max_length=100)
    is_ordered: bool = Field(default=False)
    position: int = Field(default=0)
    options: list[CaseDropdownOptionCreate] = Field(default_factory=list)


class CaseDropdownDefinitionUpdate(Schema):
    """Update an existing dropdown definition."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    ref: str | None = Field(default=None, min_length=1, max_length=255)
    icon_name: str | None = Field(default=None, max_length=100)
    is_ordered: bool | None = Field(default=None)
    position: int | None = Field(default=None)


class CaseDropdownDefinitionRead(Schema):
    """Read model for a dropdown definition with its options."""

    id: uuid.UUID
    name: str
    ref: str
    icon_name: str | None = None
    is_ordered: bool
    position: int
    options: list[CaseDropdownOptionRead] = Field(default_factory=list)


# --- Value Schemas ---


class CaseDropdownValueRead(Schema):
    """Per-case dropdown value with full definition/option info."""

    id: uuid.UUID
    definition_id: uuid.UUID
    definition_ref: str
    definition_name: str
    option_id: uuid.UUID | None = None
    option_label: str | None = None
    option_ref: str | None = None
    option_icon_name: str | None = None
    option_color: str | None = None


class CaseDropdownValueSet(Schema):
    """Request body for setting or clearing a dropdown value on a case."""

    option_id: uuid.UUID | None = Field(
        default=None,
        description="The option ID to set. Pass null to clear the value.",
    )


class CaseDropdownValueInput(Schema):
    """Dropdown selection payload for case create/update operations."""

    definition_id: uuid.UUID | None = Field(
        default=None,
        description="Dropdown definition ID.",
    )
    definition_ref: str | None = Field(
        default=None,
        description="Dropdown definition ref.",
    )
    option_id: uuid.UUID | None = Field(
        default=None,
        description="Dropdown option ID. Pass null to clear the value.",
    )
    option_ref: str | None = Field(
        default=None,
        description="Dropdown option ref. Pass null to clear the value.",
    )

    @model_validator(mode="after")
    def validate_identifiers(self) -> CaseDropdownValueInput:
        has_definition_id = self.definition_id is not None
        has_definition_ref = self.definition_ref is not None
        if has_definition_id == has_definition_ref:
            raise ValueError("Provide exactly one of definition_id or definition_ref")

        if self.option_id is not None and self.option_ref is not None:
            raise ValueError("Provide only one of option_id or option_ref")
        return self
