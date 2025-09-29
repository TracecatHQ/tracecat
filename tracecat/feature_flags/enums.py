from enum import StrEnum


class FeatureFlag(StrEnum):
    """Feature flag enum."""

    GIT_SYNC = "git-sync"
    AGENT_SANDBOX = "agent-sandbox"
    RUNBOOKS = "runbooks"
