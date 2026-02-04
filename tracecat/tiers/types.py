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
    sso: Annotated[bool, Field(description="Whether SSO is enabled")]
    git_sync: Annotated[bool, Field(description="Whether git sync is enabled")]
    agent_approvals: Annotated[
        bool,
        Field(description="Whether agent tool approvals are enabled"),
    ]
    agent_presets: Annotated[bool, Field(description="Whether agent presets are enabled")]
    case_dropdowns: Annotated[bool, Field(description="Whether case dropdowns are enabled")]
    case_durations: Annotated[bool, Field(description="Whether case durations are enabled")]
    case_tasks: Annotated[bool, Field(description="Whether case tasks are enabled")]
    case_triggers: Annotated[
        bool,
        Field(description="Whether case workflow triggers are enabled"),
    ]
