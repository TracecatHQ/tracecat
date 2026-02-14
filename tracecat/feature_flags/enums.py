from enum import StrEnum


class FeatureFlag(StrEnum):
    """Feature flag enum."""

    GIT_SYNC = "git-sync"
    AGENT_APPROVALS = "agent-approvals"
    AGENT_PRESETS = "agent-presets"
    CASE_DROPDOWNS = "case-dropdowns"
    CASE_DURATIONS = "case-durations"
    CASE_TASKS = "case-tasks"
    CASE_TRIGGERS = "case-triggers"
    REGISTRY_CLIENT = "registry-client"
    REGISTRY_SYNC_V2 = "registry-sync-v2"
    WORKFLOW_CONCURRENCY_LIMITS = "workflow-concurrency-limits"
