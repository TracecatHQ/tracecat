"""HTTP contracts and persisted documents for durable workspace synchronization."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tracecat.sync import CommitInfo, PullResourceDiff, PullResult
from tracecat.workflow.store.schemas import WorkflowSyncPullRequest
from tracecat.workspace_sync.operations.types import (
    SyncDirection,
    SyncStage,
    SyncStatus,
)
from tracecat.workspace_sync.schemas import (
    WorkspaceProjection,
    WorkspaceRemoteSnapshot,
    WorkspaceSyncExportPreview,
    WorkspaceSyncExportRequest,
)


class SyncOperationCreate(BaseModel):
    """Start a preview; reuse the client-generated ID after a lost response."""

    model_config = ConfigDict(extra="forbid")
    id: uuid.UUID
    direction: SyncDirection
    push: WorkspaceSyncExportRequest | None = Field(default=None)
    pull: WorkflowSyncPullRequest | None = Field(default=None)
    compare_ref: str | None = Field(default=None)

    @model_validator(mode="after")
    def validate_direction(self) -> "SyncOperationCreate":
        if self.direction == "push" and (self.push is None or self.pull is not None):
            raise ValueError("Push requires push inputs only")
        if self.direction == "pull" and (self.pull is None or self.push is not None):
            raise ValueError("Pull requires pull inputs only")
        return self


class SyncOperationError(BaseModel):
    """Expected operation conflict and diff lookup failures."""

    detail: str


class SyncPushResult(BaseModel):
    """Compact durable push receipt; file details stay in paginated artifacts."""

    commit: CommitInfo


class SyncOperationRead(BaseModel):
    """Small polling response, independent of the number of synced files."""

    id: uuid.UUID
    direction: SyncDirection
    status: SyncStatus
    stage: SyncStage
    created_at: datetime
    expires_at: datetime
    commit_sha: str | None = Field(default=None)
    error: str | None = Field(default=None)
    preview: WorkspaceSyncExportPreview | PullResult | None = Field(default=None)
    result: SyncPushResult | PullResult | None = Field(default=None)
    diff_count: int = Field(default=0)
    can_retry: bool = Field(default=False)
    data_applied: bool = Field(default=False)
    inputs: SyncOperationCreate


class SyncDiffPage(BaseModel):
    """A stable page of diff metadata; file text is fetched separately."""

    items: list[PullResourceDiff]
    next_cursor: str | None = Field(default=None)


class PreparedSync(BaseModel):
    """Immutable object-storage artifact reused by confirmation and retries."""

    projection: WorkspaceProjection | None = Field(default=None)
    snapshot: WorkspaceRemoteSnapshot | None = Field(default=None)
    preview: WorkspaceSyncExportPreview | PullResult
    secret_store_fingerprint: str | None = Field(default=None)
    workspace_fingerprint: str
    repository_fingerprint: str
    compare_ref: str
    compare_sha: str
    target_exists: bool = Field(default=True)
    delete_roots: list[str] = Field(default_factory=list)


class StoredSyncArtifact(BaseModel):
    """Private durable envelope including fields excluded from Git serialization."""

    prepared: PreparedSync
    snapshot_skill_contents: dict[str, dict[str, str]] = Field(default_factory=dict)
    projection_skill_contents: dict[str, dict[str, str]] = Field(default_factory=dict)
