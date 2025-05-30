from abc import ABC, abstractmethod
from typing import Any

import diskcache as dc
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_core import to_jsonable_python


class ShortTermMemory(ABC):
    """Lightweight abstract base class for short-term conversation memory management.

    - Short-term memory is defined as the memory of the current conversation only.
    - This interface is designed to be minimal and platform-agnostic.
    - We want to allow easy swapping between different persistent stores.
    """

    @abstractmethod
    def get_messages(self, conversation_id: str) -> list[ModelMessage]:
        """Get the message history for a conversation."""
        pass

    @abstractmethod
    def add_user_message(self, conversation_id: str, content: str) -> None:
        """Add a user message to the conversation."""
        pass

    @abstractmethod
    def add_assistant_message(self, conversation_id: str, content: str) -> None:
        """Add an assistant message to the conversation."""
        pass

    @abstractmethod
    def add_tool_call(
        self,
        conversation_id: str,
        tool_name: str,
        tool_args: str | dict[str, Any],
        tool_call_id: str,
    ) -> None:
        """Add a tool call to the conversation."""
        pass

    @abstractmethod
    def add_tool_result(
        self,
        conversation_id: str,
        tool_name: str,
        tool_result: str | dict[str, Any],
        tool_call_id: str,
    ) -> None:
        """Add a tool result to the conversation."""
        pass

    @abstractmethod
    def clear_conversation(self, conversation_id: str) -> None:
        """Clear all messages for a conversation."""
        pass


class FanoutCacheMemory(ShortTermMemory):
    """FanoutCache-based memory implementation."""

    def __init__(
        self, directory: str = ".cache/messages", shards: int = 8, timeout: float = 0.05
    ):
        self.cache = dc.FanoutCache(directory=directory, shards=shards, timeout=timeout, expire=21600)

    def get_messages(self, conversation_id: str) -> list[ModelMessage]:
        """Get the message history for a conversation."""
        messages = self.cache.get(conversation_id, [])
        return ModelMessagesTypeAdapter.validate_python(messages)

    def add_user_message(self, conversation_id: str, content: str) -> None:
        """Add a user message to the conversation."""
        cached_value = self.cache.get(conversation_id, [])
        messages: list[dict[str, Any]] = (
            cached_value if isinstance(cached_value, list) else []
        )

        user_prompt = ModelRequest.user_text_prompt(user_prompt=content)
        messages.append(to_jsonable_python(user_prompt))
        self.cache.set(conversation_id, messages)

    def add_assistant_message(self, conversation_id: str, content: str) -> None:
        """Add an assistant message to the conversation."""
        cached_value = self.cache.get(conversation_id, [])
        messages: list[dict[str, Any]] = (
            cached_value if isinstance(cached_value, list) else []
        )

        assistant_response = ModelResponse(parts=[TextPart(content=content)])
        messages.append(to_jsonable_python(assistant_response))
        self.cache.set(conversation_id, messages)

    def add_tool_call(
        self,
        conversation_id: str,
        tool_name: str,
        tool_args: str | dict[str, Any],
        tool_call_id: str,
    ) -> None:
        """Add a tool call to the conversation."""
        cached_value = self.cache.get(conversation_id, [])
        messages: list[dict[str, Any]] = (
            cached_value if isinstance(cached_value, list) else []
        )

        tool_call_part = ToolCallPart(
            tool_name=tool_name, args=tool_args, tool_call_id=tool_call_id
        )
        tool_call = ModelResponse(parts=[tool_call_part])
        messages.append(to_jsonable_python(tool_call))
        self.cache.set(conversation_id, messages)

    def add_tool_result(
        self, conversation_id: str, tool_name: str, tool_result: str, tool_call_id: str
    ) -> None:
        """Add a tool result to the conversation."""
        cached_value = self.cache.get(conversation_id, [])
        messages: list[dict[str, Any]] = (
            cached_value if isinstance(cached_value, list) else []
        )

        tool_return_part = ToolReturnPart(
            tool_name=tool_name, content=tool_result, tool_call_id=tool_call_id
        )
        tool_result_msg = ModelRequest(parts=[tool_return_part])
        messages.append(to_jsonable_python(tool_result_msg))
        self.cache.set(conversation_id, messages)

    def clear_conversation(self, conversation_id: str) -> None:
        """Clear all messages for a conversation."""
        self.cache.delete(conversation_id)
