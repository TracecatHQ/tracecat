"""Sync services for Git repositories."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel

from tracecat.git.models import GitUrl


@dataclass(frozen=True)
class Author:
    """Author identity used for commits."""

    name: str
    email: str


@dataclass(frozen=True)
class PushObject[T: BaseModel]:
    """A single object to push to a repository."""

    data: T
    """The model data to serialize and write"""

    path: Path | str
    """Target path in repository"""

    @property
    def path_str(self) -> str:
        """Get path as string."""
        return str(self.path)


class ConflictStrategy(StrEnum):
    """Strategy for handling workflow conflicts during import."""

    OVERWRITE = "overwrite"
    """Overwrite existing workflows with new definitions"""


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
class PushOptions:
    """Options controlling push/commit behavior."""

    message: str
    author: Author
    """Author of the commit"""

    create_pr: bool = False
    """Create a pull request if supported"""

    sign: bool = False
    """GPG signing if configured"""


@dataclass(frozen=True)
class CommitInfo:
    """Result of a push/commit operation."""

    sha: str
    """SHA of the commit"""

    ref: str
    """Resolved ref after push (e.g., branch)"""


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


class SyncService[T: BaseModel](Protocol):
    """Provider-agnostic Git/VCS sync interface.

    This abstracts transport (Git over SSH/HTTPS). Domain layers (Workflows,
    Runbooks) translate to/from TModel and call these methods.
    """

    async def pull(
        self,
        *,
        url: GitUrl,
        options: PullOptions | None = None,
    ) -> PullResult:
        """Pull objects from a repository at the given ref.

        Args:
            target: Repository and ref to read from (ref=None resolves HEAD).
            options: Optional pull options (paths, depth, LFS).

        Returns:
            A list of domain models reconstructed from repository contents.

        Raises:
            RuntimeError: Transport or checkout errors.
            ValueError: Invalid arguments (e.g., malformed URL).
        """
        ...

    async def push(
        self,
        *,
        objects: Sequence[PushObject[T]],
        url: GitUrl,
        options: PushOptions,
    ) -> CommitInfo:
        """Commit and push objects to a repository/branch.

        Args:
            objects: Domain models to serialize and write.
            url: Repository and branch/tag/SHA to write to (branch recommended).
            options: Commit metadata and optional provider hints.

        Returns:
            CommitInfo containing the resulting commit SHA and ref.

        Raises:
            RuntimeError: Transport or push errors (conflicts, auth).
            ValueError: Invalid arguments (e.g., empty commit message).
        """
        ...
