"""Default tier configuration for self-hosted deployments - unlimited everything."""

from __future__ import annotations

import os

from tracecat.tiers.enums import Entitlement
from tracecat.tiers.schemas import EffectiveEntitlements, EffectiveLimits

DEFAULT_TIER_DISPLAY_NAME = "Default"
DEV_DEFAULT_TIER_ENTITLEMENTS_ENV_VAR = "TRACECAT__DEV_DEFAULT_TIER_ENTITLEMENTS"

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
_DEFAULT_TIER_TOKEN_TO_ENTITLEMENT = {
    "custom-registry": Entitlement.CUSTOM_REGISTRY.value,
    "git-sync": Entitlement.GIT_SYNC.value,
    "agent-addons": Entitlement.AGENT_ADDONS.value,
    "case-addons": Entitlement.CASE_ADDONS.value,
    "rbac-addons": Entitlement.RBAC_ADDONS.value,
    "watchtower": Entitlement.WATCHTOWER.value,
    # Legacy flag aliases are accepted for convenience in dev environments.
    "agent-approvals": Entitlement.AGENT_ADDONS.value,
    "agent-presets": Entitlement.AGENT_ADDONS.value,
    "case-dropdowns": Entitlement.CASE_ADDONS.value,
    "case-durations": Entitlement.CASE_ADDONS.value,
    "case-tasks": Entitlement.CASE_ADDONS.value,
    "case-triggers": Entitlement.CASE_ADDONS.value,
    "rbac": Entitlement.RBAC_ADDONS.value,
}


def get_legacy_feature_flags_env() -> str | None:
    """Get legacy feature flags from TRACECAT__FEATURE_FLAGS."""
    return os.environ.get("TRACECAT__FEATURE_FLAGS")


def get_default_tier_entitlements_env() -> str | None:
    """Get default tier entitlement bootstrap values from the environment."""
    return os.environ.get(DEV_DEFAULT_TIER_ENTITLEMENTS_ENV_VAR)


def _parse_feature_flags(feature_flags_env: str) -> set[str]:
    return {
        raw_flag.strip().lower().replace("_", "-")
        for raw_flag in feature_flags_env.split(",")
        if raw_flag.strip()
    }


def resolve_default_tier_entitlement_enables(
    entitlements_env: str | None,
) -> dict[str, bool]:
    """Resolve default-tier entitlements that should be enabled from env.

    Supports comma-separated entitlement keys, legacy flag aliases, and the
    special value ``all``.
    """
    if not entitlements_env:
        return {}

    normalized_tokens = _parse_feature_flags(entitlements_env)
    if "all" in normalized_tokens:
        return {entitlement.value: True for entitlement in Entitlement}

    updates: dict[str, bool] = {}
    for token in normalized_tokens:
        if entitlement_key := _DEFAULT_TIER_TOKEN_TO_ENTITLEMENT.get(token):
            updates[entitlement_key] = True
    return updates


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
            rbac_addons=False,
            watchtower=False,
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
        watchtower=False,
    )


DEFAULT_ENTITLEMENTS = resolve_oss_default_entitlements(get_legacy_feature_flags_env())
