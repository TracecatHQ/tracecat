"""Type definitions for the tiers module."""

from __future__ import annotations

from typing import TypedDict


class EntitlementsDict(TypedDict, total=False):
    """TypedDict for tier entitlements stored in JSONB.

    All keys are optional (total=False) to support partial overrides.
    """

    custom_registry: bool
    sso: bool
    git_sync: bool
