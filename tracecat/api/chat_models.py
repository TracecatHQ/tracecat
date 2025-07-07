"""Chat API models for agent streaming."""

from typing import Any

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """Request model for starting a chat with an AI agent."""

    message: str = Field(..., description="User message to send to the agent")
    model_name: str = Field(default="gpt-4o-mini", description="AI model to use")
    model_provider: str = Field(default="openai", description="AI model provider")
    actions: list[str] = Field(
        default_factory=list,
        description="List of actions the agent can use (e.g., 'core.cases.get_case')",
    )
    instructions: str | None = Field(
        default=None, description="Optional instructions for the agent"
    )
    context: dict[str, Any] | None = Field(
        default=None, description="Optional context data for the agent"
    )


class ChatResponse(BaseModel):
    """Response model for chat initiation."""

    stream_url: str = Field(..., description="URL to connect for SSE streaming")
    conversation_id: str = Field(..., description="Unique conversation identifier")


class ChatStreamEvent(BaseModel):
    """Model for individual SSE events in the chat stream."""

    event: str = Field(..., description="Event type (e.g., 'message', 'error', 'end')")
    data: dict[str, Any] = Field(..., description="Event data payload")
    id: str | None = Field(default=None, description="Event ID for reconnection")
