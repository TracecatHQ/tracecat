from enum import StrEnum


class ChatEntity(StrEnum):
    """The type of entity associated with a chat."""

    CASE = "case"
    AGENT_PROFILE = "agent_profile"
    AGENT_PROFILE_BUILDER = "agent_profile_builder"


class MessageKind(StrEnum):
    """The type/kind of message stored in the chat."""

    CHAT_MESSAGE = "chat-message"  # Standard chat messages (user prompts, assistant responses, tool calls)
