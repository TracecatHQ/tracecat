"""Shared Tracecat-owned LLM proxy types."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

from tracecat.identifiers import OrganizationID, SessionID, WorkspaceID


class ExecutionBackend(StrEnum):
    """Runtime execution backend."""

    TRACECAT_PROXY = "tracecat_proxy"


class IngressFormat(StrEnum):
    """Public ingress formats supported by the proxy."""

    ANTHROPIC = "anthropic"


class ProviderKind(StrEnum):
    """Supported upstream provider families."""

    OPENAI = "openai"
    CUSTOM = "custom-model-provider"
    AZURE_OPENAI = "azure_openai"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"
    VERTEX_AI = "vertex_ai"
    BEDROCK = "bedrock"
    AZURE_AI = "azure_ai"


@dataclass(frozen=True, slots=True)
class NormalizedToolCall:
    """Normalized tool call across Anthropic and OpenAI request shapes."""

    id: str
    name: str
    arguments: Any = field(default_factory=dict)
    type: Literal["function"] = "function"


@dataclass(frozen=True, slots=True)
class NormalizedMessage:
    """Normalized message representation used internally by the proxy."""

    role: Literal["system", "user", "assistant", "tool"]
    content: Any = None
    tool_calls: tuple[NormalizedToolCall, ...] = ()
    tool_call_id: str | None = None
    name: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class NormalizedMessagesRequest:
    """Provider-ready messages request shared across adapter families."""

    provider: str
    model: str
    messages: tuple[NormalizedMessage, ...]
    output_format: IngressFormat
    stream: bool = False
    base_url: str | None = None
    api_version: str | None = None
    use_workspace_credentials: bool = False
    tools: tuple[dict[str, Any], ...] = ()
    tool_choice: Any = None
    parallel_tool_calls: bool | None = None
    response_format: dict[str, Any] | None = None
    model_settings: dict[str, Any] = field(default_factory=dict)
    request_id: str | None = None
    workspace_id: WorkspaceID | None = None
    organization_id: OrganizationID | None = None
    session_id: SessionID | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class NormalizedResponse:
    """Normalized upstream response before rendering to a public format."""

    provider: str
    model: str
    content: Any = None
    tool_calls: tuple[NormalizedToolCall, ...] = ()
    finish_reason: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AnthropicStreamEvent:
    """Single Anthropic-compatible SSE event."""

    event: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ProviderHTTPRequest:
    """Concrete HTTP request prepared for an upstream provider."""

    method: str
    url: str
    headers: dict[str, str]
    body: bytes | None = None
    json_body: dict[str, Any] | None = None
    stream: bool = False
