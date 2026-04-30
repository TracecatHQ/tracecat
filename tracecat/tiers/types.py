"""Type definitions for the tiers module."""

from __future__ import annotations

from typing import Annotated, TypedDict

from pydantic import Field


class EntitlementsDict(TypedDict, total=False):
    """TypedDict for tier entitlements stored in JSONB.

    All keys are optional (total=False) to support partial overrides.
    """

    custom_registry: Annotated[
        bool,
        Field(description="Whether custom registry repositories are enabled"),
    ]
    git_sync: Annotated[bool, Field(description="Whether git sync is enabled")]
    agent_addons: Annotated[
        bool,
        Field(
            description="Whether add-on agent capabilities are enabled"
            " (approvals, presets)"
        ),
    ]
    case_addons: Annotated[
        bool,
        Field(
            description="Whether add-on case capabilities are enabled"
            " (dropdowns, durations, tasks, triggers)"
        ),
    ]
    rbac_addons: Annotated[
        bool,
        Field(
            description="Whether RBAC add-ons are enabled"
            " (custom roles, groups, and assignments)"
        ),
    ]
    service_accounts: Annotated[
        bool,
        Field(
            description="Whether service accounts for API key access are enabled"
        ),
    ]
    watchtower: Annotated[
        bool,
        Field(
            description="Whether Watchtower agent monitoring is enabled"
            " (agent sessions, tool-call telemetry, and controls)"
        ),
    ]
