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


class InternalCaseTagCreate(BaseModel):
    """Internal schema for adding tags to cases with create_if_missing support."""

    tag_id: TagIdentifier = Field(
        description="Tag ID (UUID), ref, or name if create_if_missing is True",
        min_length=1,
        max_length=100,
    )
    create_if_missing: bool = Field(
        default=False,
        description="If true, create the tag if it does not exist",
    )
