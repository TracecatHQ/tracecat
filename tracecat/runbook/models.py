"""Runbook API models."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from pydantic import UUID4, BaseModel, Field, StringConstraints

from tracecat.chat.enums import ChatEntity

type RunbookAlias = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9_-]+$")]


class RunbookCreate(BaseModel):
    """Request model for creating a runbook."""

    chat_id: UUID4 | None = Field(
        default=None,
        description="ID of the chat to freeze into a runbook",
    )
    meta: dict[str, Any] | None = Field(
        default=None,
        description="Optional metadata to include with the runbook (e.g., case information)",
    )
    alias: RunbookAlias | None = Field(
        default=None,
        description="Alias for the runbook",
        min_length=3,
        max_length=50,
    )


class RunbookRead(BaseModel):
    """Model for runbook details."""

    id: UUID4 = Field(..., description="Unique runbook identifier")
    title: str = Field(..., description="Human-readable title for the runbook")
    content: str = Field(..., description="The instruction runbook string")
    tools: list[str] = Field(
        ...,
        description="The tools available to the agent for this runbook",
    )
    created_at: datetime = Field(..., description="When the runbook was created")
    updated_at: datetime = Field(..., description="When the runbook was last updated")
    meta: dict[str, Any] = Field(
        default_factory=dict,
        description="Metadata including schema version, tool SHA, token count",
    )
    summary: str | None = Field(
        default=None,
        description="A summary of the runbook.",
    )


class RunbookUpdate(BaseModel):
    """Request model for updating runbook properties."""

    title: str | None = Field(
        default=None,
        description="New title for the runbook",
        min_length=1,
        max_length=200,
    )
    content: str | None = Field(
        default=None,
        description="New content for the runbook",
        min_length=1,
        max_length=10000,
    )
    tools: list[str] | None = Field(
        default=None,
        description="New tools for the runbook",
    )
    summary: str | None = Field(
        default=None,
        description="New summary for the runbook",
        min_length=1,
        max_length=10000,
    )
    alias: RunbookAlias | None = Field(
        default=None,
        description="New alias for the runbook (must be unique within workspace)",
        min_length=3,
        max_length=50,
    )


class RunbookRunRequest(BaseModel):
    """Request model for running a runbook on cases."""

    entities: list[RunbookRunEntity] = Field(
        ..., description="Entities to run the runbook on"
    )


class RunbookRunEntity(BaseModel):
    """Request model for running a runbook on an entity."""

    entity_id: UUID4 = Field(..., description="ID of the entity to run the runbook on")
    entity_type: ChatEntity = Field(
        ..., description="Type of the entity to run the runbook on"
    )


class RunbookRunResponse(BaseModel):
    """Response model for runbook execution."""

    stream_urls: dict[str, str] = Field(
        ..., description="Mapping of chat_id to SSE stream URL"
    )
