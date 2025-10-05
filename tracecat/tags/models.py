from pydantic import BaseModel, Field

from tracecat.identifiers import TagID
from tracecat.tags.enums import TagScope


class TagRead(BaseModel):
    """Model for reading tag data with validation."""

    id: TagID
    name: str = Field(min_length=1, max_length=50)
    ref: str = Field(
        description="Slug-like identifier derived from name, used for API lookups"
    )
    color: str | None = Field(default=None, description="Hex color code")
    scope: TagScope


class TagCreate(BaseModel):
    """Model for creating new tags with validation."""

    name: str = Field(min_length=1, max_length=50)
    color: str | None = Field(default=None, description="Hex color code")
    scope: TagScope


class TagUpdate(BaseModel):
    """Model for updating existing tags with validation."""

    name: str | None = Field(default=None, min_length=1, max_length=50)
    color: str | None = Field(default=None, description="Hex color code")
    scope: TagScope | None = Field(default=None)
