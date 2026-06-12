"""Workspace sync enum values."""

from __future__ import annotations

from enum import StrEnum


class SyncProvider(StrEnum):
    GIT = "git"


class SyncResourceType(StrEnum):
    WORKFLOW = "workflow"


class SyncStateStatus(StrEnum):
    NEVER_SYNCED = "never_synced"
    CLEAN = "clean"
    LOCAL_DIRTY = "local_dirty"
    REMOTE_AHEAD = "remote_ahead"
    DIVERGED = "diverged"
    CONFLICTED = "conflicted"
    ERROR = "error"


class SyncDirection(StrEnum):
    PULL = "pull"
    PUSH = "push"


class ResourceSyncStatus(StrEnum):
    UNTRACKED = "untracked"
    SYNCED = "synced"
    LOCAL_DIRTY = "local_dirty"
    REMOTE_DIRTY = "remote_dirty"
    CONFLICTED = "conflicted"
    ERROR = "error"


class SyncOperation(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    ARCHIVE = "archive"
    DISABLE = "disable"


class ChangeSetStatus(StrEnum):
    OPEN = "open"
    VALIDATED = "validated"
    EXPORTED = "exported"
    FAILED = "failed"


class ValidationStatus(StrEnum):
    PENDING = "pending"
    VALID = "valid"
    INVALID = "invalid"


class MaterializationStatus(StrEnum):
    PENDING = "pending"
    COMMITTED = "committed"
    NO_OP = "no_op"
    FAILED = "failed"
