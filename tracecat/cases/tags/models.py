from pydantic import BaseModel, Field

from tracecat.identifiers import TagID

TagIdentifier = TagID | str  # Can be UUID or ref


class CaseTagCreate(BaseModel):
    tag_id: TagIdentifier = Field(description="Tag ID (UUID) or ref")


class CaseTagRead(BaseModel):
    """Tag data."""

    id: TagID
    name: str
    ref: str | None  # Made nullable to match schema
    color: str | None
