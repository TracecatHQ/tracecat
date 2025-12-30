from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import (
    TYPE_CHECKING,
    Any,
    Literal,
    Protocol,
    TypedDict,
    runtime_checkable,
)

import pydantic
from claude_agent_sdk.types import Message as ClaudeSDKMessage
from pydantic import TypeAdapter
from pydantic_ai import ModelResponse
from pydantic_ai.messages import ModelMessage

from tracecat import config
from tracecat.chat.enums import MessageKind

if TYPE_CHECKING:
    from pydantic_ai.tools import Tool as _PATool

    from tracecat.agent.stream.writers import StreamWriter
    from tracecat.chat.schemas import ChatMessage

    CustomToolList = list[_PATool[Any]]
else:  # pragma: no cover - runtime type hint fallback to appease pydantic
    CustomToolList = list[Any]


class MCPServerConfig(TypedDict):
    """Configuration for an MCP server."""

    url: str
    headers: dict[str, str]


class StreamKey(str):
    def __new__(
        cls,
        workspace_id: uuid.UUID | str,
        session_id: uuid.UUID | str,
        *,
        namespace: str = "agent",
    ) -> StreamKey:
        return super().__new__(
            cls,
            f"{namespace}-stream:{str(workspace_id)}:{str(session_id)}",
        )


ModelMessageTA: TypeAdapter[ModelMessage] = TypeAdapter(ModelMessage)
ModelResponseTA: TypeAdapter[ModelResponse] = TypeAdapter(ModelResponse)
ClaudeSDKMessageTA: TypeAdapter[ClaudeSDKMessage] = TypeAdapter(ClaudeSDKMessage)

# Union type for messages from either harness
UnifiedMessage = ModelMessage | ClaudeSDKMessage


@runtime_checkable
class MessageStore(Protocol):
    async def load(self, session_id: uuid.UUID) -> list[ChatMessage]: ...

    async def store(
        self,
        session_id: uuid.UUID,
        messages: Sequence[UnifiedMessage],
        *,
        kind: MessageKind = MessageKind.CHAT_MESSAGE,
    ) -> None: ...


@runtime_checkable
class StreamingAgentDeps(Protocol):
    stream_writer: StreamWriter
    message_store: MessageStore | None = None


type OutputType = (
    Literal[
        "bool",
        "float",
        "int",
        "str",
        "list[bool]",
        "list[float]",
        "list[int]",
        "list[str]",
    ]
    | dict[str, Any]
)


@pydantic.dataclasses.dataclass(kw_only=True, slots=True)
class AgentConfig:
    """Configuration for an agent."""

    # Model
    model_name: str
    model_provider: str
    base_url: str | None = None
    # Agent
    instructions: str | None = None
    output_type: OutputType | None = None
    # Tools
    actions: list[str] | None = None
    namespaces: list[str] | None = None
    tool_approvals: dict[str, bool] | None = None
    # MCP
    model_settings: dict[str, Any] | None = None
    mcp_servers: list[MCPServerConfig] | None = None
    retries: int = config.TRACECAT__AGENT_MAX_RETRIES
    deps_type: type[Any] | None = None
    custom_tools: CustomToolList | None = None
