"""Tier schemas for API request/response models."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from tracecat.core.schemas import Schema
from tracecat.tiers.types import EntitlementsDict


class EffectiveLimits(Schema):
    """Effective resource limits for an organization.

    Values are resolved from org overrides falling back to tier defaults.
    None means unlimited.
    """

    api_rate_limit: int | None = Field(
        None, description="API rate limit (requests per second). None = unlimited"
    )
    api_burst_capacity: int | None = Field(
        None, description="API burst capacity. None = unlimited"
    )
    max_concurrent_workflows: int | None = Field(
        None, description="Max concurrent running workflows. None = unlimited"
    )
    max_action_executions_per_workflow: int | None = Field(
        None,
        description="Max action executions per workflow run (runtime limit). None = unlimited",
    )
    max_concurrent_actions: int | None = Field(
        None,
        description="Max concurrent action executions across all workflows for an org. None = unlimited",
    )


class EffectiveEntitlements(Schema):
    """Effective feature entitlements for an organization.

    Values are resolved from org overrides falling back to tier defaults.
    """

    custom_registry: bool = Field(
        default=False, description="Whether custom registry repositories are enabled"
    )
    git_sync: bool = Field(default=False, description="Whether git sync is enabled")
    agent_addons: bool = Field(
        default=False,
        description="Whether add-on agent capabilities are enabled"
        " (approvals, presets)",
    )
    case_addons: bool = Field(
        default=False,
        description="Whether add-on case capabilities are enabled"
        " (dropdowns, durations, tasks, triggers)",
    )
    rbac: bool = Field(
        default=False,
        description="Whether RBAC is enabled (custom roles, groups, and assignments)",
    )


class TierRead(Schema):
    """Tier response schema."""

    id: uuid.UUID
    display_name: str
    max_concurrent_workflows: int | None
    max_action_executions_per_workflow: int | None
    max_concurrent_actions: int | None
    api_rate_limit: int | None
    api_burst_capacity: int | None
    entitlements: EntitlementsDict
    is_default: bool
    sort_order: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class TierCreate(Schema):
    """Create tier request."""

    display_name: str = Field(..., min_length=1, max_length=255)
    max_concurrent_workflows: int | None = None
    max_action_executions_per_workflow: int | None = None
    max_concurrent_actions: int | None = None
    api_rate_limit: int | None = None
    api_burst_capacity: int | None = None
    entitlements: EntitlementsDict = Field(default={})
    is_default: bool = False
    sort_order: int = 0


class TierUpdate(Schema):
    """Update tier request."""

    display_name: str | None = Field(None, min_length=1, max_length=255)
    max_concurrent_workflows: int | None = None
    max_action_executions_per_workflow: int | None = None
    max_concurrent_actions: int | None = None
    api_rate_limit: int | None = None
    api_burst_capacity: int | None = None
    entitlements: EntitlementsDict | None = None
    is_default: bool | None = None
    sort_order: int | None = None
    is_active: bool | None = None


class OrganizationTierRead(Schema):
    """Organization tier assignment response."""

    id: uuid.UUID
    organization_id: uuid.UUID
    tier_id: uuid.UUID
    max_concurrent_workflows: int | None
    max_action_executions_per_workflow: int | None
    max_concurrent_actions: int | None
    api_rate_limit: int | None
    api_burst_capacity: int | None
    entitlement_overrides: EntitlementsDict | None
    stripe_customer_id: str | None
    stripe_subscription_id: str | None
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime

    # Include resolved tier info
    tier: TierRead | None = None


class OrganizationTierUpdate(Schema):
    """Update organization tier assignment request."""

    tier_id: uuid.UUID | None = None
    max_concurrent_workflows: int | None = None
    max_action_executions_per_workflow: int | None = None
    max_concurrent_actions: int | None = None
    api_rate_limit: int | None = None
    api_burst_capacity: int | None = None
    entitlement_overrides: EntitlementsDict | None = None
    stripe_customer_id: str | None = None
    stripe_subscription_id: str | None = None
    expires_at: datetime | None = None
