"""Default tier configuration for self-hosted deployments - unlimited everything."""

from __future__ import annotations

from tracecat.tiers.schemas import EffectiveEntitlements, EffectiveLimits

# Default tier ID used in database seeding and fallbacks
DEFAULT_TIER_ID = "default"
DEFAULT_TIER_DISPLAY_NAME = "Default"

# Default limits for self-hosted deployments (None = unlimited)
DEFAULT_LIMITS = EffectiveLimits(
    api_rate_limit=None,
    api_burst_capacity=None,
    max_concurrent_workflows=None,
    max_action_executions_per_workflow=None,
    max_concurrent_actions=None,
)

# Default entitlements for self-hosted deployments (all enabled)
DEFAULT_ENTITLEMENTS = EffectiveEntitlements(
    custom_registry=True,
    sso=True,
    git_sync=True,
)
