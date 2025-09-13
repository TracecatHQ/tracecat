"""Prompt API models."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from pydantic import UUID4, BaseModel, Field, StringConstraints

from tracecat.chat.enums import ChatEntity

# Slug pattern for alias validation - alphanumeric, underscores, and hyphens (case-insensitive)
# Lowercase normalization is handled by the field validator
type PromptAlias = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9_-]+$")]


class PromptCreate(BaseModel):
    """Request model for creating a prompt from a chat."""

    chat_id: UUID4 | None = Field(
        default=None,
        description="ID of the chat to freeze into a prompt",
    )
    alias: PromptAlias | None = Field(
        default=None,
        description="Optional alias for the prompt (must be unique within workspace)",
        min_length=3,
        max_length=50,
    )
    meta: dict[str, Any] | None = Field(
        default=None,
        description="Optional metadata to include with the prompt (e.g., case information)",
    )


class PromptRead(BaseModel):
    """Model for prompt details."""

    id: UUID4 = Field(..., description="Unique prompt identifier")
    chat_id: UUID4 | None = Field(
        default=None,
        description="ID of the source chat",
    )
    title: str = Field(..., description="Human-readable title for the prompt")
    content: str = Field(..., description="The instruction prompt/runbook string")
    tools: list[str] = Field(
        ...,
        description="The tools available to the agent for this prompt",
    )
    alias: PromptAlias | None = Field(
        default=None,
        description="Alias for the prompt",
        min_length=3,
        max_length=50,
    )
    created_at: datetime = Field(..., description="When the prompt was created")
    updated_at: datetime = Field(..., description="When the prompt was last updated")
    meta: dict[str, Any] = Field(
        default_factory=dict,
        description="Metadata including schema version, tool SHA, token count",
    )
    summary: str | None = Field(
        default=None,
        description="A summary of the prompt.",
    )


class PromptUpdate(BaseModel):
    """Request model for updating prompt properties."""

    title: str | None = Field(
        default=None,
        description="New title for the prompt",
        min_length=1,
        max_length=200,
    )
    content: str | None = Field(
        default=None,
        description="New content for the prompt",
        min_length=1,
        max_length=10000,
    )
    tools: list[str] | None = Field(
        default=None,
        description="New tools for the prompt",
    )
    alias: PromptAlias | None = Field(
        default=None,
        description="New alias for the prompt (must be unique within workspace)",
        min_length=3,
        max_length=50,
    )
    summary: str | None = Field(
        default=None,
        description="New summary for the prompt",
        min_length=1,
        max_length=10000,
    )


class PromptRunRequest(BaseModel):
    """Request model for running a prompt on cases."""

    entities: list[PromptRunEntity] = Field(
        ..., description="Entities to run the prompt on"
    )


class PromptRunEntity(BaseModel):
    """Request model for running a prompt on an entity."""

    entity_id: UUID4 = Field(..., description="ID of the entity to run the prompt on")
    entity_type: ChatEntity = Field(
        ..., description="Type of the entity to run the prompt on"
    )


class PromptRunResponse(BaseModel):
    """Response model for prompt execution."""

    stream_urls: dict[str, str] = Field(
        ..., description="Mapping of case_id to SSE stream URL"
    )
