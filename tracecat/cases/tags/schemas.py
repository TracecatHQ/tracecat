from pydantic import BaseModel, Field

from tracecat.identifiers import CaseTagID

TagIdentifier = CaseTagID | str  # Can be UUID or ref


class CaseTagCreate(BaseModel):
    tag_id: TagIdentifier = Field(
        description="Tag ID (UUID) or ref",
        min_length=1,
        max_length=100,
    )


class CaseTagRead(BaseModel):
    """Tag data."""

    id: CaseTagID
    name: str
    ref: str
    color: str | None
