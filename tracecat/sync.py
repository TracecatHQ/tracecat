"""Sync services for Git repositories."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel

from tracecat.git.models import GitUrl


@dataclass(frozen=True)
class Author:
    """Author identity used for commits."""

    name: str
    email: str


@dataclass(frozen=True)
class PullOptions:
    """Options controlling pull/checkout behavior."""

    paths: list[str] | None = None  # subset of paths, if supported
    depth: int | None = 1  # shallow clone depth; None for full
    lfs: bool = False  # fetch LFS objects if needed


@dataclass(frozen=True)
class PushOptions:
    """Options controlling push/commit behavior."""

    message: str
    author: Author | None = None
    create_pr: bool = False  # optional provider feature (ignored by pure Git)
    sign: bool = False  # GPG signing if configured


@dataclass(frozen=True)
class CommitInfo:
    """Result of a push/commit operation."""

    sha: str
    ref: str  # resolved ref after push (e.g., branch)


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
    ) -> list[T]:
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
        objects: Sequence[T],
        url: GitUrl,
        options: PushOptions,
    ) -> CommitInfo:
        """Commit and push objects to a repository/branch.

        Args:
            objects: Domain models to serialize and write.
            target: Repository and branch/tag/SHA to write to (branch recommended).
            options: Commit metadata and optional provider hints.

        Returns:
            CommitInfo containing the resulting commit SHA and ref.

        Raises:
            RuntimeError: Transport or push errors (conflicts, auth).
            ValueError: Invalid arguments (e.g., empty commit message).
        """
        ...
