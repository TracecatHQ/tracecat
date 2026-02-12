"""Tier and entitlement enums."""

from enum import StrEnum


class Entitlement(StrEnum):
    """Available feature entitlements."""

    CUSTOM_REGISTRY = "custom_registry"
    SSO = "sso"
    GIT_SYNC = "git_sync"
    AGENT_APPROVALS = "agent_approvals"
    AGENT_PRESETS = "agent_presets"
    CASE_DROPDOWNS = "case_dropdowns"
    CASE_DURATIONS = "case_durations"
    CASE_TASKS = "case_tasks"
    CASE_TRIGGERS = "case_triggers"
