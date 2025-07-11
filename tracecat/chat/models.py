"""Chat API models for agent streaming."""

from datetime import datetime
from typing import Any

from pydantic import UUID4, BaseModel, Field

from tracecat.chat.enums import ChatEntity


class ChatRequest(BaseModel):
    """Request model for starting a chat with an AI agent."""

    message: str = Field(..., description="User message to send to the agent")
    model_name: str = Field(default="gpt-4o-mini", description="AI model to use")
    model_provider: str = Field(default="openai", description="AI model provider")
    instructions: str | None = Field(
        default=None, description="Optional instructions for the agent"
    )
    context: dict[str, Any] | None = Field(
        default=None, description="Optional context data for the agent"
    )


class ChatResponse(BaseModel):
    """Response model for chat initiation."""

    stream_url: str = Field(..., description="URL to connect for SSE streaming")
    chat_id: str = Field(..., description="Unique chat identifier")


class ChatStreamEvent(BaseModel):
    """Model for individual SSE events in the chat stream."""

    event: str = Field(..., description="Event type (e.g., 'message', 'error', 'end')")
    data: dict[str, Any] = Field(..., description="Event data payload")
    id: str | None = Field(default=None, description="Event ID for reconnection")


class ChatCreate(BaseModel):
    """Request model for creating a new chat."""

    title: str = Field(..., description="Human-readable title for the chat")
    entity_type: ChatEntity = Field(
        ..., description="Type of entity this chat is associated with"
    )
    entity_id: UUID4 = Field(..., description="ID of the associated entity")
    tools: list[str] | None = Field(
        default=None, description="Tools available to the agent for this chat"
    )


class ChatRead(BaseModel):
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


class ChatWithMessages(ChatRead):
    """Model for chat metadata with message history."""

    messages: list[dict[str, Any]] = Field(
        default_factory=list, description="Chat messages from Redis stream"
    )


class ChatListResponse(BaseModel):
    """Response model for listing chats."""

    chats: list[ChatRead] = Field(..., description="List of chats")
    total: int = Field(..., description="Total number of chats matching the query")


class ChatUpdate(BaseModel):
    """Request model for updating chat properties."""

    tools: list[str] | None = Field(
        default=None, description="Tools available to the agent"
    )
    title: str | None = Field(default=None, description="Chat title")
