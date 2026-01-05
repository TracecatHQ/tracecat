from enum import StrEnum


class ChatEntity(StrEnum):
    """The type of entity associated with a chat."""

    CASE = "case"
    AGENT_PRESET = "agent_preset"
    AGENT_PRESET_BUILDER = "agent_preset_builder"
    COPILOT = "copilot"


class MessageKind(StrEnum):
    """The type/kind of message stored in the chat."""

    CHAT_MESSAGE = "chat-message"  # Standard chat messages (user prompts, assistant responses, tool calls)
    APPROVAL_REQUEST = (
        "approval-request"  # System bubble requesting human approval for tool calls
    )
    APPROVAL_DECISION = (
        "approval-decision"  # User/operator decisions for pending approvals
    )
    INTERNAL = "internal"  # Internal messages not shown in chat history (e.g., continuation prompts, interrupt artifacts)
