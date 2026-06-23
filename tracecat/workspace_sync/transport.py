"""Provider-neutral VCS transport interfaces and GitHub implementation."""

from __future__ import annotations

import asyncio
import base64
import itertools
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from github.GithubException import GithubException
from github.InputGitTreeElement import InputGitTreeElement

from tracecat.db.models import User
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.git.types import GitUrl
from tracecat.registry.repositories.schemas import GitBranchInfo, GitCommitInfo
from tracecat.service import BaseWorkspaceService
from tracecat.sync import CommitInfo, PushStatus
from tracecat.vcs.github.app import GitHubAppError, GitHubAppService
from tracecat.workspace_sync.enums import VcsProvider
from tracecat.workspace_sync.schemas import (
    MANIFEST_FILENAME,
    WorkspaceManifest,
    manifest_resource_roots,
    workspace_manifest_from_json,
)
from tracecat.workspaces.service import WorkspaceService


@dataclass(frozen=True)
class VcsTreeSnapshot:
    """Snapshot of workspace sync files read from a single commit."""

    commit_sha: str
    """Resolved commit SHA the snapshot was read from."""
    tree_sha: str | None
    """SHA of the commit's root tree, when the provider exposes it."""
    files: dict[str, str]
    """Map of repository path to decoded UTF-8 file content."""


class VcsSyncTransport(Protocol):
    """Transport that can read and write workspace sync files."""

    async def read_files(self, *, url: GitUrl, ref: str) -> VcsTreeSnapshot:
        """Read workspace sync files at a ref."""
        ...

    async def write_files(
        self,
        *,
        url: GitUrl,
        files: dict[str, str],
        message: str,
        branch: str,
        create_pr: bool,
        pr_base_branch: str | None = None,
        delete_missing_paths_under: Sequence[str] = (),
    ) -> CommitInfo:
        """Commit workspace sync files to a branch."""
        ...

    async def list_commits(
        self,
        *,
        url: GitUrl,
        branch: str = "main",
        limit: int = 10,
    ) -> list[GitCommitInfo]:
        """List commits from a branch."""
        ...

    async def list_branches(
        self,
        *,
        url: GitUrl,
        limit: int = 100,
    ) -> list[GitBranchInfo]:
        """List repository branches."""
        ...


class VcsTransportFactory(Protocol):
    """Factory for provider-specific workspace sync transports."""

    def __call__(
        self,
        provider: VcsProvider,
        *,
        session: Any,
        role: Any,
    ) -> VcsSyncTransport:
        """Return a transport for the requested provider."""
        ...


def unsupported_transport(provider: VcsProvider) -> TracecatValidationError:
    """Build the error raised for providers without a sync transport yet."""
    return TracecatValidationError(
        f"{provider.value} workspace sync is not implemented yet. "
        "GitLab and Bitbucket will use token-backed VCS transports in a later pass."
    )


def vcs_transport_for_provider(
    provider: VcsProvider,
    *,
    session: Any,
    role: Any,
) -> VcsSyncTransport:
    """Return the :class:`VcsSyncTransport` for ``provider``.

    Raises :func:`unsupported_transport` for providers that are not yet
    implemented.
    """
    match provider:
        case VcsProvider.GITHUB:
            return GitHubWorkspaceSyncTransport(session=session, role=role)
        case VcsProvider.GITLAB | VcsProvider.BITBUCKET:
            raise unsupported_transport(provider)


def _normalized_roots(roots: Sequence[str]) -> tuple[str, ...]:
    """Strip surrounding slashes off each root and drop empty entries."""
    return tuple(root.strip("/") for root in roots if root.strip("/"))


def _path_is_under_roots(path: str, roots: Sequence[str]) -> bool:
    """Return whether ``path`` equals or sits beneath any of ``roots``."""
    return any(path == root or path.startswith(f"{root}/") for root in roots)


_GITHUB_BLOB_CONCURRENCY = 8
"""Maximum concurrent GitHub blob/content calls during sync reads and writes."""


class GitHubWorkspaceSyncTransport(BaseWorkspaceService):
    """GitHub App transport for workspace sync."""

    service_name = "workspace_github_sync"

    async def read_files(
        self,
        *,
        url: GitUrl,
        ref: str,
    ) -> VcsTreeSnapshot:
        """Read the manifest and managed resource files at ``ref``.

        Resolves ``ref`` to a commit, walks its tree, and decodes every blob
        under the manifest's resource roots (plus the manifest itself) into a
        :class:`VcsTreeSnapshot`. Blobs that are not valid UTF-8 are skipped.
        """
        gh_svc = GitHubAppService(session=self.session, role=self.role)
        gh = await gh_svc.get_github_client_for_repo(url)
        try:
            repo = await asyncio.to_thread(gh.get_repo, f"{url.org}/{url.repo}")
            commit = await asyncio.to_thread(repo.get_commit, ref)
            tree = await asyncio.to_thread(
                repo.get_git_tree,
                sha=commit.sha,
                recursive=True,
            )
            blob_shas = {
                item.path: item.sha
                for item in tree.tree
                if item.type == "blob" and item.path
            }
            blob_semaphore = asyncio.Semaphore(_GITHUB_BLOB_CONCURRENCY)

            async def fetch_text(path: str) -> str | None:
                """Fetch a blob and decode it as UTF-8, or ``None`` if binary."""
                async with blob_semaphore:
                    blob = await asyncio.to_thread(repo.get_git_blob, blob_shas[path])
                try:
                    return base64.b64decode(blob.content).decode("utf-8")
                except UnicodeDecodeError:
                    return None

            async def fetch_path(path: str) -> tuple[str, str | None]:
                """Fetch ``path`` and preserve its identity through gather."""
                return path, await fetch_text(path)

            files: dict[str, str] = {}
            resource_roots = manifest_resource_roots(WorkspaceManifest())
            if MANIFEST_FILENAME in blob_shas:
                manifest_content = await fetch_text(MANIFEST_FILENAME)
                if manifest_content is not None:
                    files[MANIFEST_FILENAME] = manifest_content
                    try:
                        manifest = workspace_manifest_from_json(manifest_content)
                        resource_roots = manifest_resource_roots(manifest)
                    except Exception:
                        pass

            resource_paths = [
                path
                for path in sorted(blob_shas)
                if path != MANIFEST_FILENAME
                and any(path.startswith(f"{root}/") for root in resource_roots)
            ]
            for path, content in await asyncio.gather(
                *(fetch_path(path) for path in resource_paths)
            ):
                if content is not None:
                    files[path] = content
            return VcsTreeSnapshot(
                commit_sha=commit.sha,
                tree_sha=getattr(commit.commit.tree, "sha", None),
                files=files,
            )
        except GithubException as e:
            raise GitHubAppError(f"GitHub API error: {e.status} - {e.data}") from e
        finally:
            gh.close()

    async def write_files(
        self,
        *,
        url: GitUrl,
        files: dict[str, str],
        message: str,
        branch: str,
        create_pr: bool,
        pr_base_branch: str | None = None,
        delete_missing_paths_under: Sequence[str] = (),
    ) -> CommitInfo:
        """Commit ``files`` to ``branch``, optionally opening a pull request.

        Creates ``branch`` off ``pr_base_branch`` (or the repo default) when it
        is missing, writes only the files whose content changed, and deletes any
        stale blobs under ``delete_missing_paths_under`` that are absent from
        ``files``. Returns a no-op :class:`CommitInfo` when nothing changed,
        reusing or opening a pull request when ``create_pr`` is set and the
        branch diverges from its base.
        """
        if not files:
            raise ValueError("At least one file is required for workspace sync export")
        message = message.strip()
        if not message:
            raise ValueError("A non-empty commit message is required")

        gh_svc = GitHubAppService(session=self.session, role=self.role)
        gh = await gh_svc.get_github_client_for_repo(url)
        try:
            repo = await asyncio.to_thread(gh.get_repo, f"{url.org}/{url.repo}")
            base_branch_name = pr_base_branch or url.ref or repo.default_branch
            base_branch = await asyncio.to_thread(repo.get_branch, base_branch_name)

            try:
                target_branch = await asyncio.to_thread(repo.get_branch, branch)
            except GithubException as e:
                if e.status != 404:
                    raise
                await asyncio.to_thread(
                    repo.create_git_ref,
                    ref=f"refs/heads/{branch}",
                    sha=base_branch.commit.sha,
                )
                target_branch = await asyncio.to_thread(repo.get_branch, branch)

            content_semaphore = asyncio.Semaphore(_GITHUB_BLOB_CONCURRENCY)

            async def changed_file(path: str, content: str) -> tuple[str, str] | None:
                """Return ``(path, content)`` when the branch content differs."""
                existing_content: str | None = None
                try:
                    async with content_semaphore:
                        existing = await asyncio.to_thread(
                            repo.get_contents,
                            path,
                            ref=branch,
                        )
                    if not isinstance(existing, list):
                        existing_content = base64.b64decode(existing.content).decode(
                            "utf-8"
                        )
                except GithubException as e:
                    if e.status != 404:
                        raise
                if existing_content != content:
                    return path, content
                return None

            changed_file_entries = await asyncio.gather(
                *(
                    changed_file(path, content)
                    for path, content in sorted(files.items())
                )
            )
            changed_files = {
                path: content
                for entry in changed_file_entries
                if entry is not None
                for path, content in (entry,)
            }

            stale_paths = await self._stale_paths_under_roots(
                repo=repo,
                commit_sha=target_branch.commit.sha,
                files=files,
                roots=delete_missing_paths_under,
            )

            pr_url: str | None = None
            pr_number: int | None = None
            pr_reused = False
            should_create_pr = create_pr and branch != base_branch_name
            if not changed_files and not stale_paths:
                branch_has_commits = await self._branch_has_commits_between(
                    repo=repo,
                    base_branch_name=base_branch_name,
                    branch_name=branch,
                )
                if should_create_pr and branch_has_commits:
                    pr_url, pr_number, pr_reused = await self._upsert_pull_request(
                        repo=repo,
                        url=url,
                        title=message,
                        branch_name=branch,
                        base_branch_name=base_branch_name,
                    )
                return CommitInfo(
                    status=PushStatus.NO_OP,
                    sha=None,
                    ref=branch,
                    base_ref=base_branch_name,
                    pr_url=pr_url,
                    pr_number=pr_number,
                    pr_reused=pr_reused,
                    message="No changes detected; nothing to commit.",
                )

            target_commit = await asyncio.to_thread(
                repo.get_git_commit,
                target_branch.commit.sha,
            )
            blob_create_semaphore = asyncio.Semaphore(_GITHUB_BLOB_CONCURRENCY)

            async def create_blob_element(
                path: str,
                content: str,
            ) -> InputGitTreeElement:
                """Create a Git blob and return its tree element."""
                async with blob_create_semaphore:
                    blob = await asyncio.to_thread(
                        repo.create_git_blob,
                        content,
                        "utf-8",
                    )
                return InputGitTreeElement(
                    path=path,
                    mode="100644",
                    type="blob",
                    sha=blob.sha,
                )

            elements = list(
                await asyncio.gather(
                    *(
                        create_blob_element(path, content)
                        for path, content in sorted(changed_files.items())
                    )
                )
            )
            for path in sorted(stale_paths):
                elements.append(
                    InputGitTreeElement(
                        path=path,
                        mode="100644",
                        type="blob",
                        sha=None,
                    )
                )

            tree = await asyncio.to_thread(
                repo.create_git_tree,
                elements,
                base_tree=target_commit.tree,
            )
            commit = await asyncio.to_thread(
                repo.create_git_commit,
                message,
                tree,
                [target_commit],
            )
            ref = await asyncio.to_thread(repo.get_git_ref, f"heads/{branch}")
            await asyncio.to_thread(ref.edit, sha=commit.sha)

            if should_create_pr:
                pr_url, pr_number, pr_reused = await self._upsert_pull_request(
                    repo=repo,
                    url=url,
                    title=message,
                    branch_name=branch,
                    base_branch_name=base_branch_name,
                )

            return CommitInfo(
                status=PushStatus.COMMITTED,
                sha=commit.sha,
                ref=branch,
                base_ref=base_branch_name,
                pr_url=pr_url,
                pr_number=pr_number,
                pr_reused=pr_reused,
                message="Committed workspace sync changes.",
            )
        except GithubException as e:
            raise GitHubAppError(f"GitHub API error: {e.status} - {e.data}") from e
        finally:
            gh.close()

    async def _stale_paths_under_roots(
        self,
        *,
        repo: Any,
        commit_sha: str,
        files: dict[str, str],
        roots: Sequence[str],
    ) -> set[str]:
        """Return managed blob paths at ``commit_sha`` no longer in ``files``.

        These are the stale files under ``roots`` that an export should delete so
        the branch mirrors the projected workspace exactly.
        """
        managed_roots = _normalized_roots(roots)
        if not managed_roots:
            return set()

        tree = await asyncio.to_thread(
            repo.get_git_tree,
            sha=commit_sha,
            recursive=True,
        )
        return {
            item.path
            for item in tree.tree
            if item.type == "blob"
            and item.path
            and item.path not in files
            and _path_is_under_roots(item.path, managed_roots)
        }

    async def list_commits(
        self,
        *,
        url: GitUrl,
        branch: str = "main",
        limit: int = 10,
    ) -> list[GitCommitInfo]:
        """Return up to ``limit`` most recent commits on ``branch``."""
        gh_svc = GitHubAppService(session=self.session, role=self.role)
        gh = await gh_svc.get_github_client_for_repo(url)
        try:
            repo = await asyncio.to_thread(gh.get_repo, f"{url.org}/{url.repo}")
            commits_paginated = await asyncio.to_thread(repo.get_commits, sha=branch)
            raw_commits = await asyncio.to_thread(
                lambda: list(itertools.islice(commits_paginated, limit))
            )
            return [
                GitCommitInfo(
                    sha=commit.sha,
                    message=commit.commit.message,
                    author=commit.commit.author.name or "Unknown",
                    author_email=commit.commit.author.email or "",
                    date=commit.commit.author.date.isoformat(),
                    tags=[],
                )
                for commit in raw_commits
            ]
        except GithubException as e:
            raise GitHubAppError(f"GitHub API error: {e.status} - {e.data}") from e
        finally:
            gh.close()

    async def list_branches(
        self,
        *,
        url: GitUrl,
        limit: int = 100,
    ) -> list[GitBranchInfo]:
        """Return up to ``limit`` branches, flagging the repository default."""
        gh_svc = GitHubAppService(session=self.session, role=self.role)
        gh = await gh_svc.get_github_client_for_repo(url)
        try:
            repo = await asyncio.to_thread(gh.get_repo, f"{url.org}/{url.repo}")
            branches_paginated = await asyncio.to_thread(repo.get_branches)
            raw_branches = await asyncio.to_thread(
                lambda: list(itertools.islice(branches_paginated, limit))
            )
            return [
                GitBranchInfo(
                    name=branch_obj.name,
                    is_default=branch_obj.name == repo.default_branch,
                )
                for branch_obj in raw_branches
            ]
        except GithubException as e:
            raise GitHubAppError(f"GitHub API error: {e.status} - {e.data}") from e
        finally:
            gh.close()

    async def _upsert_pull_request(
        self,
        *,
        repo: Any,
        url: GitUrl,
        title: str,
        branch_name: str,
        base_branch_name: str,
    ) -> tuple[str | None, int | None, bool]:
        """Reuse or open the sync pull request for ``branch_name``.

        Returns ``(html_url, number, reused)``, where ``reused`` is ``True`` when
        an existing open pull request was found instead of created.
        """

        def _first_open_pull_request() -> Any | None:
            """Return the first open PR from ``branch_name`` into the base, if any."""
            pulls = repo.get_pulls(
                state="open",
                head=f"{url.org}:{branch_name}",
                base=base_branch_name,
            )
            return next(iter(pulls), None)

        existing_pr = await asyncio.to_thread(_first_open_pull_request)
        if existing_pr is not None:
            return existing_pr.html_url, existing_pr.number, True

        workspace = await WorkspaceService(
            session=self.session,
            role=self.role,
        ).get_workspace(self.workspace_id)
        if workspace is None:
            raise TracecatNotFoundError("Workspace not found")

        current_user = None
        if self.role.user_id is not None:
            try:
                current_user = await self.session.get(User, self.role.user_id)
            except Exception:
                current_user = None

        published_by = current_user.email if current_user else "<unknown>"
        pr = await asyncio.to_thread(
            repo.create_pull,
            title=title,
            body=(
                "Automated workspace sync from Tracecat\n\n"
                f"**Workspace:** {workspace.name}\n"
                f"**Published by:** {published_by}"
            ),
            head=branch_name,
            base=base_branch_name,
        )
        return pr.html_url, pr.number, False

    async def _branch_has_commits_between(
        self,
        *,
        repo: Any,
        base_branch_name: str,
        branch_name: str,
    ) -> bool:
        """Return whether ``branch_name`` is ahead of ``base_branch_name``."""
        comparison = await asyncio.to_thread(
            repo.compare,
            base_branch_name,
            branch_name,
        )
        return (getattr(comparison, "ahead_by", None) or 0) > 0
