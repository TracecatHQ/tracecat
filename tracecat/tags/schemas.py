from pydantic import Field

from tracecat.core.schemas import Schema
from tracecat.identifiers import TagID


class TagRead(Schema):
    """Model for reading tag data with validation."""

    id: TagID
    name: str = Field(min_length=1, max_length=50)
    ref: str = Field(
        description="Slug-like identifier derived from name, used for API lookups"
    )
    color: str | None = Field(default=None, description="Hex color code")


class TagCreate(Schema):
    """Model for creating new tags with validation."""

    name: str = Field(min_length=1, max_length=50)
    color: str | None = Field(default=None, description="Hex color code")


class TagUpdate(Schema):
    """Model for updating existing tags with validation."""

    name: str | None = Field(default=None, min_length=1, max_length=50)
    color: str | None = Field(default=None, description="Hex color code")
