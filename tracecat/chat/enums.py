from enum import StrEnum


class ChatEntity(StrEnum):
    """The type of entity associated with a chat."""

    CASE = "case"
    AGENT_PRESET = "agent_preset"
    AGENT_PRESET_BUILDER = "agent_preset_builder"


class MessageKind(StrEnum):
    """The type/kind of message stored in the chat."""

    CHAT_MESSAGE = "chat-message"  # Standard chat messages (user prompts, assistant responses, tool calls)
