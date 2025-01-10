from pydantic import BaseModel, Field

from tracecat.identifiers import TagID


class TagRead(BaseModel):
    """Model for reading tag data with validation."""

    id: TagID
    name: str = Field(min_length=1, max_length=50)
    color: str | None = Field(default=None, description="Hex color code")


class TagCreate(BaseModel):
    """Model for creating new tags with validation."""

    name: str = Field(min_length=1, max_length=50)
    color: str | None = Field(default=None, description="Hex color code")


class TagUpdate(BaseModel):
    """Model for updating existing tags with validation."""

    name: str | None = Field(default=None, min_length=1, max_length=50)
    color: str | None = Field(default=None, description="Hex color code")
