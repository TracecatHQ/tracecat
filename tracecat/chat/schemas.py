"""Chat API models for agent streaming."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import UUID4, BaseModel, Discriminator, Field
from pydantic_ai.tools import ToolApproved, ToolDenied

from tracecat.agent.adapter import vercel
from tracecat.agent.approvals.enums import ApprovalStatus
from tracecat.agent.common.stream_types import HarnessType
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.agent.types import ClaudeSDKMessageTA, ModelMessageTA, UnifiedMessage
from tracecat.chat.enums import MessageKind

if TYPE_CHECKING:
    from tracecat.db import models


class BasicChatRequest(BaseModel):
    """Simple request model for starting a chat with a text message."""

    format: Literal["basic"] = Field(default="basic", frozen=True)
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


class VercelChatRequest(BaseModel):
    """Vercel AI SDK format request with structured UI messages."""

    kind: Literal["vercel"] = Field(default="vercel", frozen=True)
    message: vercel.UIMessage = Field(
        ..., description="User message in Vercel UI format"
    )
    model: str = Field(default="gpt-4o-mini", description="AI model to use")
    model_provider: str = Field(
        default="openai", description="AI model provider", min_length=1, max_length=50
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
    entity_type: AgentSessionEntity = Field(
        ..., description="Type of entity this chat is associated with"
    )
    entity_id: UUID4 = Field(..., description="ID of the associated entity")
    tools: list[str] | None = Field(
        default=None,
        description="Tools available to the agent for this chat",
        max_length=50,
    )


class ChatReadMinimal(BaseModel):
    """Model for chat metadata without messages.

    Note: Legacy Chat records are read-only (is_readonly=True).
    """

    id: UUID4 = Field(..., description="Unique chat identifier")
    title: str = Field(..., description="Human-readable title for the chat")
    user_id: UUID4 = Field(..., description="ID of the user who owns the chat")
    entity_type: str = Field(
        ..., description="Type of entity this chat is associated with"
    )
    entity_id: UUID4 = Field(..., description="ID of the associated entity")
    tools: list[str] = Field(..., description="Tools available to the agent")
    agent_preset_id: uuid.UUID | None = Field(
        default=None,
        description="Agent preset used for this chat, if any",
    )
    created_at: datetime = Field(..., description="When the chat was created")
    updated_at: datetime = Field(..., description="When the chat was last updated")
    last_stream_id: str | None = Field(
        default=None,
        description="Last processed Redis stream ID for this chat",
    )
    is_readonly: bool = Field(
        default=True,
        description="Whether this chat is read-only (legacy chats cannot be modified)",
    )


class ChatRead(ChatReadMinimal):
    """Model for chat metadata with message history."""

    messages: list[ChatMessage] = Field(
        default_factory=list, description="Chat messages from Redis stream"
    )


class ChatReadVercel(ChatReadMinimal):
    """Model for chat metadata with message history in Vercel format."""

    messages: list[vercel.UIMessage] = Field(
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
    agent_preset_id: uuid.UUID | None = Field(
        default=None,
        description="Agent preset to use for the chat session (set to null for default instructions)",
    )


class ApprovalRead(BaseModel):
    """Response schema for approval data in chat timeline."""

    id: uuid.UUID
    tool_call_id: str
    tool_name: str
    tool_call_args: dict[str, Any] | None = None
    status: ApprovalStatus
    reason: str | None = None
    decision: bool | dict[str, Any] | None = None
    approved_by: uuid.UUID | None = None
    approved_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ChatMessage(BaseModel):
    """Model for a chat message with typed message payload.

    This model supports both regular messages and approval bubbles:
    - kind=CHAT_MESSAGE: Contains message field with user/assistant content
    - kind=APPROVAL_REQUEST/APPROVAL_DECISION: Contains approval field with approval data
    """

    id: str = Field(..., description="Unique message identifier")
    kind: MessageKind = Field(
        default=MessageKind.CHAT_MESSAGE,
        description="Message kind for rendering",
    )
    message: UnifiedMessage | None = Field(
        default=None,
        description="The deserialized message (for kind=CHAT_MESSAGE)",
    )
    approval: ApprovalRead | None = Field(
        default=None,
        description="Approval data for approval bubble rendering (for kind=APPROVAL_REQUEST/APPROVAL_DECISION)",
    )

    @classmethod
    def from_db(cls, db_msg: models.ChatMessage) -> ChatMessage:
        """Deserialize a database message into a typed ChatMessage."""
        if db_msg.harness == HarnessType.CLAUDE_CODE.value:
            message = ClaudeSDKMessageTA.validate_python(db_msg.data)
        else:
            message = ModelMessageTA.validate_python(db_msg.data)
        return cls(id=str(db_msg.id), message=message)


# --- Approvals (CE Handshake) -------------------------------------------------


class ApprovalItem(BaseModel):
    """Single pending approval request for a tool call."""

    tool_call_id: str = Field(..., description="The unique tool call ID")
    tool_name: str = Field(..., description="Fully-qualified tool name")
    args: dict[str, Any] | str | None = Field(
        default=None, description="Original args proposed by the model"
    )


class AwaitingApproval(BaseModel):
    """Normalized API envelope when a run is awaiting approvals."""

    status: Literal["awaiting_approval"] = Field(default="awaiting_approval")
    session_id: uuid.UUID
    items: list[ApprovalItem]


class ApprovalDecision(BaseModel):
    """Operator decision for a pending approval."""

    tool_call_id: str
    action: Literal["approve", "override", "deny"]
    override_args: dict[str, Any] | None = None
    reason: str | None = None

    def to_deferred_result(self) -> bool | ToolApproved | ToolDenied:
        match self.action:
            case "approve":
                return True
            case "override":
                return ToolApproved(override_args=self.override_args or {})
            case "deny":
                return ToolDenied(message=self.reason or "The tool call was denied.")
        # Fallback shouldn't be hit due to Literal typing
        return False


class ContinueRunRequest(BaseModel):
    """Payload to continue a CE run after collecting approvals."""

    kind: Literal["continue"] = Field(default="continue", frozen=True)
    decisions: list[ApprovalDecision]


# Union type for chat requests - supports both simple and Vercel formats
ChatRequest = Annotated[VercelChatRequest | ContinueRunRequest, Discriminator("kind")]
