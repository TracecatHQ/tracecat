"""Shared types for bulk invitation creation."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum

# Maximum emails accepted in a single bulk invitation request. Bounds request
# size at both the schema (validation) and service (defensive) layers.
MAX_BULK_INVITE_EMAILS = 100


class BatchInviteStatus(StrEnum):
    """Per-email outcome of a bulk invitation request."""

    CREATED = "created"  # A new or refreshed pending invitation was issued.
    SKIPPED = "skipped"  # Already a member, or a live pending invite exists.


@dataclass(frozen=True, slots=True)
class BatchInviteItem:
    """Outcome for a single email in a bulk invitation request."""

    email: str
    status: BatchInviteStatus
    reason: str | None = None
    # Populated for created items so the caller can build the invitation email.
    invitation_id: uuid.UUID | None = None
    token: str | None = None
