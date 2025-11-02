from enum import StrEnum


class FeatureFlag(StrEnum):
    """Feature flag enum."""

    GIT_SYNC = "git-sync"
    AGENT_SANDBOX = "agent-sandbox"
    AGENT_APPROVALS = "agent-approvals"
    CASE_DURATIONS = "case-durations"
    CASE_TASKS = "case-tasks"
