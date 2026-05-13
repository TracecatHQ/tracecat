"""Pydantic schemas for agent tag resources."""

from pydantic import BaseModel

from tracecat.core.schemas import Schema
from tracecat.identifiers import AgentTagID


class AgentTagRead(Schema):
    """Tag data."""

    id: AgentTagID
    name: str
    ref: str
    color: str | None


class AgentPresetTagCreate(BaseModel):
    """Payload for adding a tag to an agent preset."""

    tag_id: AgentTagID
