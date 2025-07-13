"""Prompt API models."""

from datetime import datetime
from typing import Any

from pydantic import UUID4, BaseModel, Field


class PromptCreate(BaseModel):
    """Request model for creating a prompt from a chat."""

    chat_id: UUID4 = Field(..., description="ID of the chat to freeze into a prompt")


class PromptRead(BaseModel):
    """Model for prompt details."""

    id: UUID4 = Field(..., description="Unique prompt identifier")
    chat_id: UUID4 = Field(..., description="ID of the source chat")
    title: str = Field(..., description="Human-readable title for the prompt")
    content: str = Field(..., description="The instruction prompt/agenda string")
    tools: list[str] = Field(
        ...,
        description="The tools available to the agent for this prompt",
    )
    created_at: datetime = Field(..., description="When the prompt was created")
    meta: dict[str, Any] = Field(
        default_factory=dict,
        description="Metadata including schema version, tool SHA, token count",
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


class PromptRunRequest(BaseModel):
    """Request model for running a prompt on cases."""

    case_ids: list[UUID4] = Field(
        ...,
        description="List of case IDs to run the prompt on",
        min_length=1,
        max_length=100,
    )


class PromptRunResponse(BaseModel):
    """Response model for prompt execution."""

    stream_urls: dict[str, str] = Field(
        ..., description="Mapping of case_id to SSE stream URL"
    )
