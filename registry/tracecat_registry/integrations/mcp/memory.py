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
    async def get_messages(self, conversation_id: str) -> list[ModelMessage]:
        """Get the message history for a conversation."""
        pass

    @abstractmethod
    async def add_user_message(self, conversation_id: str, content: str) -> None:
        """Add a user message to the conversation."""
        pass

    @abstractmethod
    async def add_assistant_message(self, conversation_id: str, content: str) -> None:
        """Add an assistant message to the conversation."""
        pass

    @abstractmethod
    async def add_tool_call(
        self,
        conversation_id: str,
        tool_name: str,
        tool_args: str | dict[str, Any],
        tool_call_id: str,
    ) -> None:
        """Add a tool call to the conversation."""
        pass

    @abstractmethod
    async def add_tool_result(
        self, conversation_id: str, tool_name: str, result: str, tool_call_id: str
    ) -> None:
        """Add a tool result to the conversation."""
        pass

    @abstractmethod
    async def clear_conversation(self, conversation_id: str) -> None:
        """Clear all messages for a conversation."""
        pass


class FanoutCacheMemory(ShortTermMemory):
    """FanoutCache-based memory implementation."""

    def __init__(
        self, directory: str = ".cache/messages", shards: int = 8, timeout: float = 0.05
    ):
        self.cache = dc.FanoutCache(directory=directory, shards=shards, timeout=timeout)

    async def get_messages(self, conversation_id: str) -> list[ModelMessage]:
        """Get the message history for a conversation."""
        messages = self.cache.get(conversation_id, [])
        return ModelMessagesTypeAdapter.validate_python(messages)

    async def add_user_message(self, conversation_id: str, content: str) -> None:
        """Add a user message to the conversation."""
        messages: list[dict[str, Any]] = self.cache.get(conversation_id, [])  # type: ignore
        user_prompt = ModelRequest.user_text_prompt(user_prompt=content)
        messages.append(to_jsonable_python(user_prompt))
        self.cache.set(conversation_id, messages)

    async def add_assistant_message(self, conversation_id: str, content: str) -> None:
        """Add an assistant message to the conversation."""
        messages: list[dict[str, Any]] = self.cache.get(conversation_id, [])  # type: ignore
        assistant_response = ModelResponse(parts=[TextPart(content=content)])
        messages.append(to_jsonable_python(assistant_response))
        self.cache.set(conversation_id, messages)

    async def add_tool_call(
        self,
        conversation_id: str,
        tool_name: str,
        tool_args: str | dict[str, Any],
        tool_call_id: str,
    ) -> None:
        """Add a tool call to the conversation."""
        messages: list[dict[str, Any]] = self.cache.get(conversation_id, [])  # type: ignore
        parts = [
            ToolCallPart(tool_name=tool_name, args=tool_args, tool_call_id=tool_call_id)
        ]
        tool_call = ModelResponse(parts=parts)  # type: ignore
        messages.append(to_jsonable_python(tool_call))
        self.cache.set(conversation_id, messages)

    async def add_tool_result(
        self, conversation_id: str, tool_name: str, result: str, tool_call_id: str
    ) -> None:
        """Add a tool result to the conversation."""
        messages: list[dict[str, Any]] = self.cache.get(conversation_id, [])  # type: ignore
        parts = [
            ToolReturnPart(
                tool_name=tool_name, content=result, tool_call_id=tool_call_id
            )
        ]
        tool_result_msg = ModelRequest(parts=parts)  # type: ignore
        messages.append(to_jsonable_python(tool_result_msg))
        self.cache.set(conversation_id, messages)

    async def clear_conversation(self, conversation_id: str) -> None:
        """Clear all messages for a conversation."""
        self.cache.delete(conversation_id)
