"""Agent types module.

This module re-exports types from common/types.py for backward compatibility,
and contains legacy TypeAdapter utilities for pydantic-ai integration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from claude_agent_sdk.types import Message as ClaudeSDKMessage
from pydantic import TypeAdapter


if TYPE_CHECKING:
    from pydantic_ai.messages import ModelMessage

    from tracecat.agent.stream.writers import StreamWriter

# --- Legacy TypeAdapters for pydantic-ai ---
# These are used by the legacy pydantic-ai harness, not the sandbox runtime


ClaudeSDKMessageTA: TypeAdapter[ClaudeSDKMessage] = TypeAdapter(ClaudeSDKMessage)


class _LazyTypeAdapter:
    """Lazy wrapper for TypeAdapter that imports pydantic-ai only when used."""

    def __init__(self, import_path: str, type_name: str):
        self._import_path = import_path
        self._type_name = type_name
        self._adapter: TypeAdapter[Any] | None = None

    def _ensure_adapter(self) -> TypeAdapter[Any]:
        if self._adapter is None:
            import importlib

            module = importlib.import_module(self._import_path)
            type_cls = getattr(module, self._type_name)
            self._adapter = TypeAdapter(type_cls)
        return self._adapter

    def validate_python(self, obj: Any) -> Any:
        return self._ensure_adapter().validate_python(obj)

    def dump_python(self, obj: Any, **kwargs: Any) -> Any:
        return self._ensure_adapter().dump_python(obj, **kwargs)

    def validate_json(self, data: bytes | str) -> Any:
        return self._ensure_adapter().validate_json(data)

    def dump_json(self, obj: Any, **kwargs: Any) -> bytes:
        return self._ensure_adapter().dump_json(obj, **kwargs)


# Lazy TypeAdapters that only import pydantic-ai when methods are called
ModelMessageTA: Any = _LazyTypeAdapter("pydantic_ai.messages", "ModelMessage")
ModelResponseTA: Any = _LazyTypeAdapter("pydantic_ai", "ModelResponse")

# Union type for messages from either harness
# At runtime, ModelMessage is Any so this is effectively Any | ClaudeSDKMessage
UnifiedMessage = ModelMessage | ClaudeSDKMessage


@runtime_checkable
class StreamingAgentDeps(Protocol):
    stream_writer: StreamWriter

