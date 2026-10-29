"""Compact messages for one complete workspace sync lifecycle."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from tracecat.auth.types import Role
from tracecat.workspace_sync.operations.schemas import SyncOperationCreate
from tracecat.workspace_sync.operations.types import SyncStage, SyncStatus

type SyncReceiptStatus = Literal["committed", "uncommitted", "missing"]


@dataclass(frozen=True, slots=True)
class SyncLifecycleInput:
    inputs: SyncOperationCreate
    role: Role
    fingerprint: str


@dataclass(frozen=True, slots=True)
class SyncPreparedRef:
    artifact_key: str
    commit_sha: str
    diff_count: int
    read_scopes: list[str]
    expires_at: datetime
    valid: bool = True
    write_scopes: list[str] | None = None


@dataclass(frozen=True, slots=True)
class SyncCommand:
    command_id: uuid.UUID
    preview_revision: int
    state_revision: int


@dataclass(frozen=True, slots=True)
class SyncLifecycleState:
    status: SyncStatus
    stage: SyncStage
    revision: int
    created_at: datetime
    expires_at: datetime
    preview: SyncPreparedRef | None = None
    preview_revision: int = 0
    can_retry: bool = False
    data_applied: bool = False
    error: str | None = None


@dataclass(frozen=True, slots=True)
class SyncLifecycleSnapshot:
    start: SyncLifecycleInput
    state: SyncLifecycleState


@dataclass(frozen=True, slots=True)
class SyncApplyInput:
    start: SyncLifecycleInput
    preview: SyncPreparedRef


@dataclass(frozen=True, slots=True)
class SyncCommandReceipt:
    kind: Literal["apply", "retry"]
    command: SyncCommand
    state: SyncLifecycleState
