from enum import StrEnum


class FeatureFlag(StrEnum):
    """Feature flag enum."""

    GIT_SYNC = "git-sync"
    AGENT_APPROVALS = "agent-approvals"
    AGENT_PRESETS = "agent-presets"
    CASE_DROPDOWNS = "case-dropdowns"
    CASE_DURATIONS = "case-durations"
    CASE_TASKS = "case-tasks"
