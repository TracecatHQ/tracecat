"""Default limits and entitlements for OSS/self-hosted deployments."""

from __future__ import annotations

import os
from importlib.metadata import entry_points

from tracecat.agent.backends.constants import AGENT_BACKEND_ENTRY_POINT_GROUP
from tracecat.tiers.schemas import EffectiveEntitlements, EffectiveLimits

DEFAULT_TIER_DISPLAY_NAME = "Default"

# Default limits for self-hosted deployments (None = unlimited)
DEFAULT_LIMITS = EffectiveLimits(
    api_rate_limit=None,
    api_burst_capacity=None,
    max_concurrent_workflows=None,
    max_action_executions_per_workflow=None,
    max_concurrent_actions=None,
)


_AGENT_ADDON_FLAGS = ("agent-approvals", "agent-presets")
_CASE_ADDON_FLAGS = ("case-dropdowns", "case-durations", "case-tasks", "case-triggers")
_RBAC_FLAGS = ("rbac",)


def get_legacy_feature_flags_env() -> str | None:
    """Get legacy feature flags from TRACECAT__FEATURE_FLAGS."""
    return os.environ.get("TRACECAT__FEATURE_FLAGS")


def has_installed_agent_backend() -> bool:
    """Whether a package registers an agent backend besides the built-in one.

    The built-in backend is not an entry point, so any registration is extra.
    Entry points are read without loading them to keep this import cheap.
    """
    return bool(entry_points(group=AGENT_BACKEND_ENTRY_POINT_GROUP))


def _parse_feature_flags(feature_flags_env: str) -> set[str]:
    return {
        raw_flag.strip().lower().replace("_", "-")
        for raw_flag in feature_flags_env.split(",")
        if raw_flag.strip()
    }


# Default entitlements for OSS/single-tenant deployments.
def resolve_oss_default_entitlements(
    feature_flags_env: str | None,
    *,
    agent_backend_installed: bool = False,
) -> EffectiveEntitlements:
    """Resolve OSS default entitlements from legacy feature flags.

    Fresh OSS installs start with custom registry and workspace chat enabled.
    Existing OSS deployments can preserve prior behavior by mapping enabled
    feature flags to their corresponding entitlement groups.

    Agent presets, skills, MCP catalog connectors, tool approvals, and the agent
    inbox are open source; ``agent_addons`` only gates agent folders, tags, and
    case agent runs. Agent channels (Slack and Microsoft Teams, including approvals
    from those channels) are closed source and gated by ``agent_channels``; the
    legacy ``agent-channels`` feature flag maps to it. Semantic table search is
    closed source and gated by ``semantic_search``. Forms are gated by ``forms``;
    the ``forms`` feature flag maps to it, so one token turns on both the flag
    and the entitlement on single-tenant deployments. Non-default agent backends
    are gated by ``agent_runtime_preview``, which is on whenever one is
    installed, since installing a backend is how a deployment opts in.

    Multi-workspace is disabled on both paths. Existing workspaces remain
    accessible; the entitlement only gates creation beyond the first workspace.
    """
    # Fresh install path.
    if not feature_flags_env:
        return EffectiveEntitlements(
            custom_registry=True,
            git_sync=False,
            agent_addons=False,
            case_addons=False,
            rbac_addons=False,
            service_accounts=False,
            workspace_chat=True,
            multi_workspace=False,
            watchtower=False,
            external_secret_stores=False,
            agent_channels=False,
            semantic_search=False,
            forms=False,
            tunnels=False,
            agent_runtime_preview=agent_backend_installed,
        )

    # Existing install path: map legacy feature flags to entitlement groups.
    normalized_flags = _parse_feature_flags(feature_flags_env)
    git_sync_enabled = "git-sync" in normalized_flags

    agent_addons_enabled = False
    for flag in _AGENT_ADDON_FLAGS:
        if flag in normalized_flags:
            agent_addons_enabled = True
            break

    case_addons_enabled = False
    for flag in _CASE_ADDON_FLAGS:
        if flag in normalized_flags:
            case_addons_enabled = True
            break

    agent_channels_enabled = "agent-channels" in normalized_flags
    forms_enabled = "forms" in normalized_flags

    rbac_enabled = False
    for flag in _RBAC_FLAGS:
        if flag in normalized_flags:
            rbac_enabled = True
            break

    return EffectiveEntitlements(
        custom_registry=True,
        git_sync=git_sync_enabled,
        agent_addons=agent_addons_enabled,
        case_addons=case_addons_enabled,
        rbac_addons=rbac_enabled,
        service_accounts=False,
        workspace_chat=True,
        multi_workspace=False,
        watchtower=False,
        external_secret_stores=False,
        agent_channels=agent_channels_enabled,
        semantic_search=False,
        forms=forms_enabled,
        tunnels=False,
        agent_runtime_preview=agent_backend_installed,
    )


DEFAULT_ENTITLEMENTS = resolve_oss_default_entitlements(
    get_legacy_feature_flags_env(),
    agent_backend_installed=has_installed_agent_backend(),
)
