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
