"""Sync services for Git repositories."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal


class PushStatus(StrEnum):
    """Status of a push/commit operation."""

    COMMITTED = "committed"
    NO_OP = "no_op"


@dataclass(frozen=True)
class PullOptions:
    """Options controlling pull/checkout behavior."""

    commit_sha: str | None = None
    """Specific commit SHA to pull from"""

    paths: list[str] | None = None
    """Subset of paths, if supported"""

    depth: int | None = 1
    """Shallow clone depth; None for full"""

    lfs: bool = False
    """Fetch LFS objects if needed"""

    dry_run: bool = False
    """Validate only, don't perform actual import"""


@dataclass(frozen=True)
class CommitInfo:
    """Result of a push/commit operation."""

    status: PushStatus
    """Outcome status for the push operation."""

    sha: str | None
    """SHA of the commit; None for no-op pushes."""

    ref: str
    """Resolved ref after push (e.g., branch)"""

    base_ref: str
    """Resolved base branch used for branch creation and PR operations."""

    pr_url: str | None = None
    """Created or reused pull request URL if available."""

    pr_number: int | None = None
    """Created or reused pull request number if available."""

    pr_reused: bool = False
    """Whether an existing PR was reused instead of creating a new one."""

    message: str = ""
    """Human-readable summary of the push outcome."""


@dataclass(frozen=True)
class PullDiagnostic:
    """Diagnostic information about workflow import issues."""

    workflow_path: str
    """Path to the workflow file in repository"""

    workflow_title: str | None
    """Title of the workflow, if parseable"""

    error_type: Literal[
        "conflict",
        "validation",
        "dependency",
        "parse",
        "github",
        "system",
        "transaction",
    ]
    """Type of error: 'conflict', 'validation', 'dependency', 'parse', 'github', 'system', 'transaction'"""

    message: str
    """Human-readable error message"""

    details: dict[str, Any]
    """Additional error details for debugging"""


def serializable_validation_errors(
    errors: Sequence[Any],
) -> list[dict[str, Any]]:
    """Return Pydantic validation errors with non-serializable values stringified."""
    normalized: list[dict[str, Any]] = []
    for error in errors:
        safe_error = _json_safe(error)
        if isinstance(safe_error, dict):
            normalized.append(safe_error)
        else:
            normalized.append({"error": safe_error})
    return normalized


def _json_safe(value: Any) -> Any:
    """Convert nested diagnostic values to JSON-serializable primitives."""
    if isinstance(value, dict):
        return {str(key): _json_safe(inner) for key, inner in value.items()}
    if isinstance(value, list):
        return [_json_safe(inner) for inner in value]
    if isinstance(value, tuple):
        return [_json_safe(inner) for inner in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)


@dataclass(frozen=True)
class ResourcePullCount:
    """Per-resource pull counts for workspace sync imports."""

    found: int
    """Number of resources found in the repository snapshot."""

    imported: int
    """Number of resources imported into the workspace."""


@dataclass(frozen=True)
class SyncPreviewResource:
    """Displayable workspace sync resource included in a preview."""

    resource_type: str
    """Workspace sync resource type."""

    source_id: str
    """Stable Git source identifier for the resource."""

    name: str
    """Human-readable resource name."""

    path: str
    """Primary repository path for the resource."""


@dataclass(frozen=True)
class PullResourceDiff:
    """Text diff for one workspace-sync resource file."""

    resource_type: str
    """Workspace sync resource type."""

    source_id: str
    """Stable source identifier from the repository."""

    source_path: str
    """Repository path for the changed resource file."""

    change_type: Literal["added", "modified", "deleted"]
    """Whether sync would create, update, or delete a resource file.

    Pull is currently upsert-only: local resources absent from the incoming Git
    snapshot are left untouched, so dry-run diffs do not report deletions.
    """

    title: str | None
    """Human-readable resource label when available."""

    diff: str
    """Unified text diff between current and target resource file content."""

    truncated: bool = False
    """Whether the diff was shortened for response size."""


@dataclass(frozen=True)
class PullResult:
    """Result of a pull operation with atomic guarantees."""

    success: bool
    """Whether the entire pull operation succeeded"""

    commit_sha: str
    """The commit SHA that was pulled from"""

    workflows_found: int
    """Total number of workflow definitions found"""

    workflows_imported: int
    """Number of workflows actually imported (0 if failed, equals found if success)"""

    diagnostics: list[PullDiagnostic]
    """List of issues found (empty if success)"""

    message: str
    """Summary message about the operation"""

    resource_counts: dict[str, ResourcePullCount] | None = None
    """Optional per-resource counts for workspace-level sync operations."""

    resource_diffs: list[PullResourceDiff] | None = None
    """Optional changed resource file diffs for dry-run pull previews."""

    files: list[str] | None = None
    """Optional repository-relative files included in a pull preview."""

    resources: list[SyncPreviewResource] | None = None
    """Optional displayable resources included in a pull preview."""
