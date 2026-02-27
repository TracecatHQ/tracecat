"""Tier-specific exceptions."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from tracecat.exceptions import TracecatException, TracecatNotFoundError

if TYPE_CHECKING:
    from tracecat.identifiers import OrganizationID


class TierError(TracecatException):
    """Base exception for tier-related errors."""


class TierNotFoundError(TracecatNotFoundError):
    """Raised when a tier is not found."""


class OrganizationNotFoundError(TracecatNotFoundError):
    """Raised when an organization is not found."""

    def __init__(self, org_id: OrganizationID):
        super().__init__(f"Organization {org_id} not found")
        self.org_id = org_id


class DefaultTierNotConfiguredError(TierError):
    """Raised when no default tier is configured."""

    def __init__(self):
        super().__init__("No default tier configured. Run database migrations.")


class CannotDeleteDefaultTierError(TierError):
    """Raised when attempting to delete the default tier."""

    def __init__(self):
        super().__init__("Cannot delete the default tier")


class TierInUseError(TierError):
    """Raised when attempting to delete a tier that has organizations assigned."""

    def __init__(self, tier_id: uuid.UUID):
        super().__init__(
            f"Cannot delete tier '{tier_id}': organizations are still assigned to it"
        )
        self.tier_id = tier_id


class TierLimitExceeded(TierError):
    """Base exception for tier limit violations."""

    def __init__(self, limit_name: str, current: int, limit: int):
        super().__init__(f"Tier limit '{limit_name}' exceeded: {current}/{limit}")
        self.limit_name = limit_name
        self.current = current
        self.limit = limit


class InvalidOrganizationConcurrencyCapError(TierError):
    """Raised when an effective concurrency cap is configured as non-positive."""

    def __init__(self, *, scope: str, org_id: OrganizationID, limit: int):
        super().__init__(
            "Invalid organization concurrency cap: "
            f"scope={scope} org_id={org_id} limit={limit}"
        )
        self.scope = scope
        self.org_id = org_id
        self.limit = limit


class ConcurrentWorkflowLimitExceeded(TierLimitExceeded):
    """Raised when max_concurrent_workflows limit is exceeded."""

    def __init__(self, current: int, limit: int):
        super().__init__("max_concurrent_workflows", current, limit)


class ActionExecutionLimitExceeded(TierLimitExceeded):
    """Raised when max_action_executions_per_workflow limit is exceeded."""

    def __init__(self, current: int, limit: int):
        super().__init__("max_action_executions_per_workflow", current, limit)
