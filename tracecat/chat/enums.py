from enum import StrEnum


class ChatEntity(StrEnum):
    """The type of entity associated with a chat."""

    CASE = "case"
    RUNBOOK = "runbook"


class MessageKind(StrEnum):
    """The type/kind of message stored in the chat."""

    CHAT_MESSAGE = "chat-message"  # Standard chat messages (user prompts, assistant responses, tool calls)
