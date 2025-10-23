from enum import StrEnum


class FeatureFlag(StrEnum):
    """Feature flag enum."""

    GIT_SYNC = "git-sync"
    AGENT_SANDBOX = "agent-sandbox"
    RUNBOOKS = "runbooks"
    CASE_DURATIONS = "case-durations"
    CASE_TASKS = "case-tasks"
