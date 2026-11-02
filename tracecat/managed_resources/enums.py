"""Managed resource enum values."""

from __future__ import annotations

from enum import StrEnum


class ManagedTargetStatus(StrEnum):
    """Outcome of the most recent rollout to a managed target workspace."""

    PENDING = "pending"
    UP_TO_DATE = "up_to_date"
    BLOCKED = "blocked"
    NEEDS_REFERENCES = "needs_references"
    FAILED = "failed"


class ManagedReferenceKind(StrEnum):
    """Kind of source-side reference a target workspace must resolve locally."""

    MODEL = "model"
    MCP_INTEGRATION = "mcp_integration"
    SECRET_STORE = "secret_store"


class ManagedRolloutKind(StrEnum):
    """What a rollout record does to its target workspace."""

    APPLY = "apply"
    """Apply the files at a tag as managed resources."""
    UNASSIGN = "unassign"
    """Delete every managed resource and stop targeting the workspace."""


class ManagedRolloutStatus(StrEnum):
    """Lifecycle of one rollout record for one target workspace."""

    PREVIEWING = "previewing"
    READY = "ready"
    """Previewed; nothing blocks applying."""
    NO_CHANGES = "no_changes"
    """Previewed; the target already matches the tag."""
    BLOCKED = "blocked"
    """A non-managed resource conflicts, or a registry install cannot run."""
    NEEDS_REFERENCES = "needs_references"
    """A model, MCP integration or secret store still needs a match."""
    APPLYING = "applying"
    APPLIED = "applied"
    FAILED = "failed"
    STALE = "stale"
    """A newer preview replaced this one, or the target changed after it."""
