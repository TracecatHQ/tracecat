"""Pydantic schemas for skill tag resources."""

from pydantic import BaseModel

from tracecat.core.schemas import Schema
from tracecat.identifiers import SkillTagID


class SkillTagRead(Schema):
    """Tag data."""

    id: SkillTagID
    name: str
    ref: str
    color: str | None


class SkillTagCreate(BaseModel):
    """Payload for adding a tag to a skill."""

    tag_id: SkillTagID
