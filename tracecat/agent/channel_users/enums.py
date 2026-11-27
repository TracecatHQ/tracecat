from enum import StrEnum


class ChannelAccessPolicy(StrEnum):
    """Who may act on an agent through a channel; set per bot, never versioned."""

    LINKED_ONLY = "linked_only"
    """Only linked org members may message, stop, or approve."""
    LINKED_APPROVALS = "linked_approvals"
    """Anyone may message or stop; approving needs a linked member."""
    OPEN = "open"
    """No linking needed."""


class ChannelAction(StrEnum):
    """What a channel sender asks the agent to do."""

    MESSAGE = "message"
    STOP = "stop"
    APPROVE = "approve"


class ChannelLinkError(StrEnum):
    """Why linking failed; the link page maps each to fixed copy."""

    INVALID = "invalid"
    """Malformed, expired, or already used."""
    NOT_MEMBER = "not_member"
    OTHER_USER = "other_user"
    WRONG_ACCOUNT = "wrong_account"
    """The provider user who signed in is not the token's."""
    CANCELLED = "cancelled"
    SIGNED_OUT = "signed_out"
    FAILED = "failed"
    """The provider refused the sign-in or errored."""
    NO_BOT = "no_bot"
    """The signed-in provider tenant has no active bot in the organization."""
    BLOCKED_NETWORK = "blocked_network"
    """The organization's IP allowlist excludes the browser."""
