from enum import StrEnum


class WorkspaceErrorDetailsPolicy(StrEnum):
    """How secret error withholding applies to a workspace's actions."""

    WITHHOLD = "withhold"
    """Always withhold error details when secrets are in scope."""

    PER_ACTION = "per_action"
    """Actions may opt in individually via `unsafe_disable_secret_error_withholding`."""

    DISABLED = "disabled"
    """Withholding is disabled for every action in the workspace."""
