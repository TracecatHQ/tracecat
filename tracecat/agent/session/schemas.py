"""Pydantic schemas for agent session API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from tracecat.agent.adapter.vercel import UIMessage
from tracecat.agent.common.stream_types import HarnessType
from tracecat.agent.session.types import AgentSessionEntity


class AgentSessionCreate(BaseModel):
    """Request schema for creating an agent session."""

    id: uuid.UUID | None = Field(
        default=None,
        description="Session ID. If not provided, service generates one.",
    )
    # Metadata fields
    title: str = Field(
        default="New Chat",
        description="Human-readable title for the session",
        min_length=1,
        max_length=200,
    )
    created_by: uuid.UUID | None = Field(
        default=None,
        description="User who created this session",
    )
    entity_type: AgentSessionEntity = Field(
        ...,
        description="Type of entity this session is associated with",
    )
    entity_id: uuid.UUID = Field(
        ...,
        description="ID of the associated entity",
    )
    tools: list[str] | None = Field(
        default=None,
        description="Tools available to the agent for this session",
        max_length=50,
    )
    agent_preset_id: uuid.UUID | None = Field(
        default=None,
        description="Agent preset used for this session (if any)",
    )
    # Harness fields
    harness_type: HarnessType = Field(
        default=HarnessType.CLAUDE_CODE, description="Agent harness type"
    )


class AgentSessionUpdate(BaseModel):
    """Request schema for updating an agent session."""

    title: str | None = Field(
        default=None, description="Session title", min_length=1, max_length=200
    )
    tools: list[str] | None = Field(
        default=None, description="Tools available to the agent", max_length=50
    )
    agent_preset_id: uuid.UUID | None = Field(
        default=None, description="Agent preset to use for this session"
    )
    harness_type: HarnessType | None = Field(
        default=None, description="Agent harness type"
    )


class AgentSessionHistoryRead(BaseModel):
    """Response schema for agent session history entries."""

    id: uuid.UUID
    session_id: uuid.UUID
    content: dict[str, Any] = Field(..., description="JSONL line content")
    kind: str = Field(
        default="internal",
        description="Message kind for filtering (chat-message, internal)",
    )
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AgentSessionRead(BaseModel):
    """Response schema for agent session."""

    id: uuid.UUID
    workspace_id: uuid.UUID
    # Metadata
    title: str
    created_by: uuid.UUID | None
    entity_type: str
    entity_id: uuid.UUID
    tools: list[str] | None
    agent_preset_id: uuid.UUID | None
    # Harness
    harness_type: str | None
    # Stream tracking
    last_stream_id: str | None = None
    # Fork tracking
    parent_session_id: uuid.UUID | None = None
    # Timestamps
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AgentSessionReadWithMessages(AgentSessionRead):
    """Response schema for agent session with message history."""

    # Import at runtime to avoid circular imports
    # ChatMessage is defined in chat/schemas.py
    messages: list = Field(default_factory=list, description="Session messages")


class AgentSessionReadVercel(AgentSessionRead):
    """Response schema for agent session with Vercel format messages."""

    messages: list[UIMessage] = Field(
        default_factory=list, description="Session messages in Vercel UI format"
    )


class AgentSessionForkRequest(BaseModel):
    """Request schema for forking an agent session."""

    entity_type: AgentSessionEntity | None = Field(
        default=None,
        description="Override entity type for the forked session. "
        "Use 'approval' for inbox forks to hide from main chat list.",
    )
