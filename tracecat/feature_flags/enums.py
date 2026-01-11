from enum import StrEnum


class FeatureFlag(StrEnum):
    """Feature flag enum."""

    GIT_SYNC = "git-sync"
    AGENT_APPROVALS = "agent-approvals"
    AGENT_PRESETS = "agent-presets"
    CASE_DURATIONS = "case-durations"
    CASE_TASKS = "case-tasks"
    EXECUTOR_AUTH = "executor-auth"
    REGISTRY_CLIENT = "registry-client"
    REGISTRY_SYNC_V2 = "registry-sync-v2"
    ACTION_STATEMENT_ACTIVITY = "action-statement-activity"
    """Use consolidated handle_action_statement_activity for action execution.

    Bundles run_if eval, scatter/gather, and action execution into one activity.
    Reduces Temporal history events from ~4 per action to 1.
    """
