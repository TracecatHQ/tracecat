"""Schemas for agent tags. Reuses the generic Tag* shapes."""

from __future__ import annotations

from pydantic import BaseModel

from tracecat.identifiers import AgentTagID
from tracecat.tags.schemas import TagCreate, TagRead, TagUpdate

# Re-export under agent-namespaced names for client clarity, while reusing
# the validated shapes from the generic tags module. Tag schemas are
# entity-agnostic (name, ref, color), so duplicating the definitions adds
# no value.
AgentTagRead = TagRead
AgentTagCreate = TagCreate
AgentTagUpdate = TagUpdate


class AgentPresetTagCreate(BaseModel):
    """Payload for attaching a tag to a preset."""

    tag_id: AgentTagID
