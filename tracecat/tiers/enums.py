"""Tier and entitlement enums."""

from enum import StrEnum


class Entitlement(StrEnum):
    """Available feature entitlements."""

    CUSTOM_REGISTRY = "custom_registry"
    GIT_SYNC = "git_sync"
    AGENT_ADDONS = "agent_addons"
    CASE_ADDONS = "case_addons"
    RBAC_ADDONS = "rbac_addons"
    SERVICE_ACCOUNTS = "service_accounts"
    WORKSPACE_CHAT = "workspace_chat"
    MULTI_WORKSPACE = "multi_workspace"
    WATCHTOWER = "watchtower"
    EXTERNAL_SECRET_STORES = "external_secret_stores"
    AGENT_CHANNELS = "agent_channels"
    SEMANTIC_SEARCH = "semantic_search"
    FORMS = "forms"
    TUNNELS = "tunnels"
    AGENT_RUNTIME_PREVIEW = "agent_runtime_preview"
    AGENT_RUNTIME_WORKFLOWS = "agent_runtime_workflows"
    AGENT_CHANNELS_PREVIEW = "agent_channels_preview"
    OBJECT_TRIGGERS = "object_triggers"


class ConcurrencyLimitName(StrEnum):
    """Per-organization concurrency limits, named after their tier fields."""

    MAX_CONCURRENT_WORKFLOWS = "max_concurrent_workflows"
    MAX_CONCURRENT_ACTIONS = "max_concurrent_actions"
    MAX_CONCURRENT_TOOL_CALLS = "max_concurrent_tool_calls"
    MAX_CONCURRENT_AGENT_TURNS = "max_concurrent_agent_turns"
