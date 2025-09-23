"""Chat API models for agent streaming."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic import UUID4, BaseModel, Field
from pydantic_ai.messages import ModelMessage

from tracecat.agent.runtime import ModelMessageTA
from tracecat.chat.enums import ChatEntity

if TYPE_CHECKING:
    from tracecat.db.schemas import ChatMessage as DBChatMessage


class ChatRequest(BaseModel):
    """Request model for starting a chat with an AI agent."""

    message: str = Field(
        ...,
        description="User message to send to the agent",
        min_length=1,
        max_length=10000,
    )
    model_name: str = Field(
        default="gpt-4o-mini",
        description="AI model to use",
        min_length=1,
        max_length=100,
    )
    model_provider: str = Field(
        default="openai", description="AI model provider", min_length=1, max_length=50
    )
    instructions: str | None = Field(
        default=None, description="Optional instructions for the agent", max_length=5000
    )
    context: dict[str, Any] | None = Field(
        default=None, description="Optional context data for the agent"
    )
    base_url: str | None = Field(
        default=None,
        description="Optional base URL for the model provider",
        max_length=500,
    )


class ChatResponse(BaseModel):
    """Response model for chat initiation."""

    stream_url: str = Field(..., description="URL to connect for SSE streaming")
    chat_id: uuid.UUID = Field(..., description="Unique chat identifier")


class ChatCreate(BaseModel):
    """Request model for creating a new chat."""

    title: str = Field(
        ...,
        description="Human-readable title for the chat",
        min_length=1,
        max_length=200,
    )
    entity_type: ChatEntity = Field(
        ..., description="Type of entity this chat is associated with"
    )
    entity_id: UUID4 = Field(..., description="ID of the associated entity")
    tools: list[str] | None = Field(
        default=None,
        description="Tools available to the agent for this chat",
        max_length=50,
    )


class ChatReadMinimal(BaseModel):
    """Model for chat metadata without messages."""

    id: UUID4 = Field(..., description="Unique chat identifier")
    title: str = Field(..., description="Human-readable title for the chat")
    user_id: UUID4 = Field(..., description="ID of the user who owns the chat")
    entity_type: str = Field(
        ..., description="Type of entity this chat is associated with"
    )
    entity_id: UUID4 = Field(..., description="ID of the associated entity")
    tools: list[str] = Field(..., description="Tools available to the agent")
    created_at: datetime = Field(..., description="When the chat was created")
    updated_at: datetime = Field(..., description="When the chat was last updated")
    last_stream_id: str | None = Field(
        default=None,
        description="Last processed Redis stream ID for this chat",
    )


class ChatRead(ChatReadMinimal):
    """Model for chat metadata with message history."""

    messages: list[ChatMessage] = Field(
        default_factory=list, description="Chat messages from Redis stream"
    )


class ChatUpdate(BaseModel):
    """Request model for updating chat properties."""

    tools: list[str] | None = Field(
        default=None, description="Tools available to the agent", max_length=50
    )
    title: str | None = Field(
        default=None, description="Chat title", min_length=1, max_length=200
    )


class ChatMessage(BaseModel):
    """Model for chat metadata with a single message."""

    id: str = Field(..., description="Unique chat identifier")
    message: ModelMessage = Field(..., description="The message from the chat")

    @classmethod
    def from_db(cls, value: DBChatMessage) -> ChatMessage:
        return cls(
            id=str(value.id),
            message=ModelMessageTA.validate_python(value.data),
        )
