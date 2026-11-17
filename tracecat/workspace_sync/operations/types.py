"""Small Temporal messages; resource contents never enter workflow history."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, TypedDict

from pydantic import JsonValue

from tracecat.auth.types import Role

if TYPE_CHECKING:
    from tracecat.workspace_sync.transport import VcsTreeSnapshot

type SyncDirection = Literal["push", "pull"]
type SyncStatus = Literal[
    "queued", "running", "ready", "applying", "completed", "failed", "expired"
]
type SyncStage = Literal[
    "fetching",
    "preparing",
    "awaiting_confirmation",
    "applying",
    "finishing",
    "finished",
]


@dataclass(frozen=True, slots=True)
class SyncOperationRef:
    operation_id: uuid.UUID
    role: Role
    phase: Literal["preview", "apply"]
    attempt: int = 0


class StaleSyncPreviewError(Exception):
    """The prepared state can no longer be safely applied."""


class SyncOperationConflictError(Exception):
    """The operation is incompatible with the requested transition."""


class SyncOperationStartError(Exception):
    """Temporal could not confirm that the operation started."""


VALIDATION_FAILED_MESSAGE = (
    "Validation failed. Review the inputs and start a fresh preview."
)
_MAX_VALIDATION_MESSAGE_LENGTH = 500


def validation_message(exc: BaseException) -> str:
    """Return a bounded, single-line form of a user-facing validation error."""
    text = " ".join(str(exc).split())
    if not text:
        return VALIDATION_FAILED_MESSAGE
    if len(text) > _MAX_VALIDATION_MESSAGE_LENGTH:
        return text[: _MAX_VALIDATION_MESSAGE_LENGTH - 1] + "…"
    return text


@dataclass(frozen=True, slots=True)
class SyncFailure:
    ref: SyncOperationRef
    reason: Literal["stale", "authorization", "validation", "transient"]
    message: str | None = None


class SyncPreviewSummary(TypedDict):
    """Small persisted preview index, with contents held in object storage."""

    preview: JsonValue
    diff_count: int
    read_scopes: list[str]
    write_scopes: list[str]


def lock_key(value: str) -> int:
    """Stable PostgreSQL advisory lock key shared by activity phases."""
    return int.from_bytes(
        hashlib.sha256(value.encode()).digest()[:8], "big", signed=True
    )


@dataclass(frozen=True, slots=True)
class FetchedSync:
    """Remote bytes captured before opening the local preview transaction."""

    remote: VcsTreeSnapshot
    repository_fingerprint: str
    compare_ref: str
    target_exists: bool
