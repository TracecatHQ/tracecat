"""Default tier configuration for self-hosted deployments - unlimited everything."""

from __future__ import annotations

import os

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


def get_legacy_feature_flags_env() -> str | None:
    """Get legacy feature flags from TRACECAT__FEATURE_FLAGS."""
    return os.environ.get("TRACECAT__FEATURE_FLAGS")


def _parse_feature_flags(feature_flags_env: str) -> set[str]:
    return {
        raw_flag.strip().lower().replace("_", "-")
        for raw_flag in feature_flags_env.split(",")
        if raw_flag.strip()
    }


# Default entitlements for OSS/single-tenant deployments.
def resolve_oss_default_entitlements(
    feature_flags_env: str | None,
) -> EffectiveEntitlements:
    """Resolve OSS default entitlements from legacy feature flags.

    Fresh OSS installs should start with only custom registry enabled.
    Existing OSS deployments can preserve prior behavior by mapping enabled
    feature flags to their corresponding entitlement groups.
    """
    # Fresh install path.
    if not feature_flags_env:
        return EffectiveEntitlements(
            custom_registry=True,
            git_sync=False,
            agent_addons=False,
            case_addons=False,
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

    return EffectiveEntitlements(
        custom_registry=True,
        git_sync=git_sync_enabled,
        agent_addons=agent_addons_enabled,
        case_addons=case_addons_enabled,
    )


DEFAULT_ENTITLEMENTS = resolve_oss_default_entitlements(get_legacy_feature_flags_env())
