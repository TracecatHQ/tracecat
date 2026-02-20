"""Tier and entitlement enums."""

from enum import StrEnum


class Entitlement(StrEnum):
    """Available feature entitlements."""

    CUSTOM_REGISTRY = "custom_registry"
    GIT_SYNC = "git_sync"
    AGENT_ADDONS = "agent_addons"
    CASE_ADDONS = "case_addons"
