"""Prompt API models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import UUID4, BaseModel, Field

from tracecat.chat.enums import ChatEntity


class PromptCreate(BaseModel):
    """Request model for creating a prompt from a chat."""

    chat_id: UUID4 = Field(..., description="ID of the chat to freeze into a prompt")


class PromptRead(BaseModel):
    """Model for prompt details."""

    id: UUID4 = Field(..., description="Unique prompt identifier")
    chat_id: UUID4 = Field(..., description="ID of the source chat")
    title: str = Field(..., description="Human-readable title for the prompt")
    content: str = Field(..., description="The instruction prompt/runbook string")
    tools: list[str] = Field(
        ...,
        description="The tools available to the agent for this prompt",
    )
    created_at: datetime = Field(..., description="When the prompt was created")
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
