"""Provider-neutral VCS transport interfaces and GitHub implementation."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import itertools
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import quote, urlparse

import httpx
from github.GithubException import GithubException
from github.InputGitTreeElement import InputGitTreeElement
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from tracecat.db.models import User
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.git.types import GitUrl
from tracecat.registry.repositories.schemas import GitBranchInfo, GitCommitInfo
from tracecat.service import BaseWorkspaceService
from tracecat.sync import CommitInfo, PushStatus
from tracecat.vcs.github.app import GitHubAppError, GitHubAppService
from tracecat.vcs.gitlab.app import GitLabApiError, GitLabError, GitLabTokenService
from tracecat.vcs.gitlab.schemas import GitLabTokenCredentials
from tracecat.vcs.gitlab.types import (
    GitLabBranch,
    GitLabCommit,
    GitLabCommitAction,
    GitLabCompareParams,
    GitLabCompareResult,
    GitLabCreateBranchParams,
    GitLabCreateCommitPayload,
    GitLabCreateMergeRequestPayload,
    GitLabListCommitsParams,
    GitLabListMergeRequestsParams,
    GitLabMergeRequest,
    GitLabProject,
    GitLabTreeEntry,
    GitLabTreeParams,
)
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
    blob_paths: frozenset[str] = field(default_factory=frozenset)
    """All blob paths present in the tree, including non-UTF-8 blobs."""


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
        "Bitbucket will use a token-backed VCS transport in a later pass."
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
        case VcsProvider.GITLAB:
            return GitLabWorkspaceSyncTransport(session=session, role=role)
        case VcsProvider.BITBUCKET:
            raise unsupported_transport(provider)


def _normalized_roots(roots: Sequence[str]) -> tuple[str, ...]:
    """Strip surrounding slashes off each root and drop empty entries."""
    return tuple(root.strip("/") for root in roots if root.strip("/"))


def _path_is_under_roots(path: str, roots: Sequence[str]) -> bool:
    """Return whether ``path`` equals or sits beneath any of ``roots``."""
    return any(path == root or path.startswith(f"{root}/") for root in roots)


_GITHUB_BLOB_CONCURRENCY = 8
"""Maximum concurrent GitHub blob/content calls during sync reads and writes."""

_GITHUB_TREE_CHUNK_SIZE = 128
"""Balance request count against GitHub create-tree timeouts (observed as 422)."""

_GITHUB_TREE_CHUNK_MAX_BYTES = 2 * 1024 * 1024
"""Cap the serialized payload per create-tree call.

Tree entries embed file content inline, so entry count alone does not bound
request size. A single entry larger than the cap still ships alone because an
entry cannot be split."""

_GITHUB_RETRY_BUDGET_SECONDS = 45.0
"""Maximum shared Retry-After delay budget for one GitHub export."""

_GITHUB_RATE_LIMIT_HEADER_NAMES = (
    "x-ratelimit-limit",
    "x-ratelimit-remaining",
    "x-ratelimit-used",
    "x-ratelimit-reset",
    "x-ratelimit-resource",
)
"""GitHub response headers retained in structured exporter error logs."""

_GITLAB_PAGE_SIZE = 100
"""GitLab REST page size used for workspace sync reads."""

_GITLAB_BLOB_CONCURRENCY = 8
"""Maximum concurrent GitLab blob calls during sync reads."""


@dataclass
class _GitHubRetryBudget:
    """Mutable Retry-After delay budget shared by one GitHub export."""

    remaining_seconds: float


def _git_blob_sha(content: str) -> str:
    """Return the Git blob object SHA for UTF-8 ``content``."""
    content_bytes = content.encode("utf-8")
    framed_content = b"blob " + str(len(content_bytes)).encode() + b"\0" + content_bytes
    return hashlib.sha1(framed_content, usedforsecurity=False).hexdigest()


async def _github_write_with_retry[T](
    call: Callable[[], T],
    *,
    budget: _GitHubRetryBudget,
) -> T:
    """Run a GitHub write call, honoring Retry-After within ``budget``.

    GitHub signals rate limiting with either 403 or 429. A nonpositive
    Retry-After is re-raised rather than retried so a persistent zero-delay
    response cannot spin without consuming budget, and a Retry-After larger
    than the remaining budget is re-raised rather than truncated — retrying
    before the requested delay elapses can extend secondary throttling.
    """
    while True:
        try:
            return await asyncio.to_thread(call)
        except GithubException as e:
            headers = {key.lower(): value for key, value in (e.headers or {}).items()}
            retry_after_header = headers.get("retry-after")
            if (
                e.status not in (403, 429)
                or retry_after_header is None
                or budget.remaining_seconds <= 0
            ):
                raise
            try:
                retry_after = float(retry_after_header)
            except (TypeError, ValueError):
                raise
            if retry_after <= 0 or retry_after > budget.remaining_seconds:
                raise
            budget.remaining_seconds -= retry_after
            await asyncio.sleep(retry_after)


class BaseWorkspaceSyncTransport(BaseWorkspaceService):
    """Shared, provider-neutral helpers for workspace sync transports.

    The GitHub and GitLab transports differ in how they authenticate, list
    blobs, and write commits, but agree on which files a snapshot contains, how
    export inputs are validated, and what the generated PR/MR body says. Those
    provider-neutral pieces live here so both transports stay in lockstep.
    """

    @staticmethod
    def _normalize_commit_message(files: dict[str, str], message: str) -> str:
        """Validate export inputs and return the stripped commit message."""
        if not files:
            raise ValueError("At least one file is required for workspace sync export")
        message = message.strip()
        if not message:
            raise ValueError("A non-empty commit message is required")
        return message

    async def _select_snapshot_files(
        self,
        *,
        blob_paths: Sequence[str],
        fetch_text: Callable[[str], Awaitable[str | None]],
    ) -> dict[str, str]:
        """Decode the manifest and managed-resource blobs from a commit tree.

        ``blob_paths`` lists every blob path in the tree; ``fetch_text`` loads and
        UTF-8 decodes one path, returning ``None`` for binary blobs. The manifest
        (when present) selects the resource roots; only paths under those roots
        are read. Shared by the GitHub and GitLab readers, which differ only in
        how blobs are listed and fetched.
        """
        paths = set(blob_paths)

        async def fetch_path(path: str) -> tuple[str, str | None]:
            """Fetch ``path`` and preserve its identity through gather."""
            return path, await fetch_text(path)

        files: dict[str, str] = {}
        resource_roots = manifest_resource_roots(WorkspaceManifest())
        if MANIFEST_FILENAME in paths:
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
            for path in sorted(paths)
            if path != MANIFEST_FILENAME
            and any(path.startswith(f"{root}/") for root in resource_roots)
        ]
        for path, content in await asyncio.gather(
            *(fetch_path(path) for path in resource_paths)
        ):
            if content is not None:
                files[path] = content
        return files

    async def _sync_request_body(self) -> str:
        """Render the shared PR/MR description with workspace attribution."""
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
        return (
            "Automated workspace sync from Tracecat\n\n"
            f"**Workspace:** {workspace.name}\n"
            f"**Published by:** {published_by}"
        )


class GitHubWorkspaceSyncTransport(BaseWorkspaceSyncTransport):
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
            # GitHub's git/trees endpoint keys off the tree SHA, not the commit
            # SHA; passing the commit SHA 404s for many repositories.
            tree_sha = commit.commit.tree.sha
            tree = await asyncio.to_thread(
                repo.get_git_tree,
                sha=tree_sha,
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

            files = await self._select_snapshot_files(
                blob_paths=list(blob_shas),
                fetch_text=fetch_text,
            )
            return VcsTreeSnapshot(
                commit_sha=commit.sha,
                tree_sha=tree_sha,
                files=files,
                blob_paths=frozenset(blob_shas),
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
        message = self._normalize_commit_message(files, message)

        gh_svc = GitHubAppService(session=self.session, role=self.role)
        gh = await gh_svc.get_github_client_for_repo(url)
        github_stage = "resolve_repository"
        github_endpoint = "GET /repos/{owner}/{repo}"
        retry_budget = _GitHubRetryBudget(_GITHUB_RETRY_BUDGET_SECONDS)
        try:
            repo = await asyncio.to_thread(gh.get_repo, f"{url.org}/{url.repo}")
            github_stage = "prepare_branch"
            github_endpoint = "GET|POST /repos/{owner}/{repo}/branches|git/refs"
            base_branch_name = pr_base_branch or url.ref or repo.default_branch
            if create_pr and branch == base_branch_name:
                raise TracecatValidationError(
                    "create_pr exports must target a non-base branch"
                )
            base_branch = await asyncio.to_thread(repo.get_branch, base_branch_name)

            try:
                target_branch = await asyncio.to_thread(repo.get_branch, branch)
            except GithubException as e:
                if e.status != 404:
                    raise
                await _github_write_with_retry(
                    lambda: repo.create_git_ref(
                        ref=f"refs/heads/{branch}",
                        sha=base_branch.commit.sha,
                    ),
                    budget=retry_budget,
                )
                target_branch = await asyncio.to_thread(repo.get_branch, branch)

            github_stage = "resolve_target_commit"
            github_endpoint = "GET /repos/{owner}/{repo}/git/commits/{sha}"
            target_commit = await asyncio.to_thread(
                repo.get_git_commit,
                target_branch.commit.sha,
            )
            github_stage = "read_target_tree"
            github_endpoint = "GET /repos/{owner}/{repo}/git/trees/{sha}"
            target_tree = await asyncio.to_thread(
                repo.get_git_tree,
                sha=target_commit.tree.sha,
                recursive=True,
            )
            target_tree_truncated = target_tree.raw_data.get("truncated") is True
            target_blob_shas = {
                item.path: item.sha
                for item in target_tree.tree
                if item.type == "blob" and item.path
            }
            if target_tree_truncated:
                changed_files = dict(files)
                stale_paths: set[str] = set()
                self.logger.warning(
                    "Skipping GitHub workspace sync stale-path deletion because "
                    "the recursive tree response was truncated",
                    tree_sha=target_commit.tree.sha,
                )
            else:
                changed_files = {
                    path: content
                    for path, content in files.items()
                    if target_blob_shas.get(path) != _git_blob_sha(content)
                }
                stale_paths = self._stale_paths_under_roots(
                    tree_entries=target_tree.tree,
                    files=files,
                    roots=delete_missing_paths_under,
                )

            pr_url: str | None = None
            pr_number: int | None = None
            pr_reused = False
            self.logger.info(
                "Prepared GitHub workspace sync changes",
                changed_file_count=len(changed_files),
                deleted_file_count=len(stale_paths),
            )
            if not changed_files and not stale_paths:
                github_stage = "reuse_pull_request"
                github_endpoint = "GET|POST /repos/{owner}/{repo}/compare|pulls"
                branch_has_commits = await self._branch_has_commits_between(
                    repo=repo,
                    base_branch_name=base_branch_name,
                    branch_name=branch,
                )
                if create_pr and branch_has_commits:
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

            elements = [
                InputGitTreeElement(
                    path=path,
                    mode="100644",
                    type="blob",
                    content=content,
                )
                for path, content in sorted(changed_files.items())
            ]
            elements.extend(
                InputGitTreeElement(path=path, mode="100644", type="blob", sha=None)
                for path in sorted(stale_paths)
            )
            element_chunks: list[list[InputGitTreeElement]] = []
            current_chunk: list[InputGitTreeElement] = []
            current_chunk_bytes = 0
            for element in elements:
                element_bytes = len(json.dumps(element._identity).encode("utf-8"))
                if current_chunk and (
                    len(current_chunk) >= _GITHUB_TREE_CHUNK_SIZE
                    or current_chunk_bytes + element_bytes
                    > _GITHUB_TREE_CHUNK_MAX_BYTES
                ):
                    element_chunks.append(current_chunk)
                    current_chunk = []
                    current_chunk_bytes = 0
                current_chunk.append(element)
                current_chunk_bytes += element_bytes
            if current_chunk:
                element_chunks.append(current_chunk)
            # Mirror PyGithub's Requester serialization (json.dumps with default
            # separators) so the logged size matches the transmitted payload.
            tree_payload_size_bytes = sum(
                len(
                    json.dumps(
                        {
                            "base_tree": target_commit.tree.sha,
                            "tree": [element._identity for element in chunk],
                        }
                    ).encode("utf-8")
                )
                for chunk in element_chunks
            )

            self.logger.info(
                "Creating GitHub workspace sync tree",
                tree_entry_count=len(elements),
                tree_chunk_count=len(element_chunks),
                tree_payload_size_bytes=tree_payload_size_bytes,
            )
            tree = target_commit.tree
            for chunk_index, chunk in enumerate(element_chunks, start=1):
                github_stage = "create_tree_chunk"
                github_endpoint = "POST /repos/{owner}/{repo}/git/trees"
                base_tree = tree
                tree = await _github_write_with_retry(
                    lambda chunk=chunk, base_tree=base_tree: repo.create_git_tree(
                        chunk,
                        base_tree=base_tree,
                    ),
                    budget=retry_budget,
                )
                self.logger.info(
                    "Created GitHub workspace sync tree chunk",
                    tree_chunk_index=chunk_index,
                    tree_chunk_count=len(element_chunks),
                    tree_entry_count=len(chunk),
                    tree_sha=tree.sha,
                )

            github_stage = "create_commit"
            github_endpoint = "POST /repos/{owner}/{repo}/git/commits"
            commit = await _github_write_with_retry(
                lambda: repo.create_git_commit(
                    message,
                    tree,
                    [target_commit],
                ),
                budget=retry_budget,
            )
            github_stage = "resolve_target_ref"
            github_endpoint = "GET /repos/{owner}/{repo}/git/ref/heads/{branch}"
            ref = await asyncio.to_thread(repo.get_git_ref, f"heads/{branch}")
            github_stage = "update_target_ref"
            github_endpoint = "PATCH /repos/{owner}/{repo}/git/refs/heads/{branch}"
            await _github_write_with_retry(
                lambda: ref.edit(sha=commit.sha),
                budget=retry_budget,
            )
            self.logger.info(
                "Updated GitHub workspace sync ref",
                tree_sha=tree.sha,
                commit_sha=commit.sha,
                ref=branch,
            )

            if create_pr:
                github_stage = "upsert_pull_request"
                github_endpoint = "GET|POST /repos/{owner}/{repo}/pulls"
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
            headers = {key.lower(): value for key, value in (e.headers or {}).items()}
            self.logger.error(
                "GitHub workspace sync request failed",
                stage=github_stage,
                github_endpoint=github_endpoint,
                github_status=e.status,
                github_request_id=headers.get("x-github-request-id"),
                github_retry_after=headers.get("retry-after"),
                github_rate_limit_headers={
                    name: headers[name]
                    for name in _GITHUB_RATE_LIMIT_HEADER_NAMES
                    if name in headers
                },
            )
            raise GitHubAppError(f"GitHub API error: {e.status} - {e.data}") from e
        finally:
            gh.close()

    def _stale_paths_under_roots(
        self,
        *,
        tree_entries: Sequence[Any],
        files: dict[str, str],
        roots: Sequence[str],
    ) -> set[str]:
        """Return managed blob paths in ``tree_entries`` no longer in ``files``.

        These are the stale files under ``roots`` that an export should delete so
        the branch mirrors the projected workspace exactly.
        """
        managed_roots = _normalized_roots(roots)
        if not managed_roots:
            return set()

        return {
            item.path
            for item in tree_entries
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

        pr = await asyncio.to_thread(
            repo.create_pull,
            title=title,
            body=await self._sync_request_body(),
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


class GitLabWorkspaceSyncTransport(BaseWorkspaceSyncTransport):
    """GitLab token-backed REST transport for workspace sync."""

    service_name = "workspace_gitlab_sync"

    async def read_files(
        self,
        *,
        url: GitUrl,
        ref: str,
    ) -> VcsTreeSnapshot:
        """Read the manifest and managed resource files at ``ref``."""
        async with self._authed_client(url) as client:
            return await self._read_files_with_client(client=client, url=url, ref=ref)

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
        """Commit ``files`` to ``branch``, optionally opening a merge request."""
        message = self._normalize_commit_message(files, message)

        async with self._authed_client(url) as client:
            project_id = _gitlab_project_id(url)
            base_branch_name = pr_base_branch or url.ref
            if base_branch_name is None:
                project = await self._get_project(client=client, project_id=project_id)
                base_branch_name = project.default_branch or "main"
            if create_pr and branch == base_branch_name:
                raise TracecatValidationError(
                    "create_pr exports must target a non-base branch"
                )

            await self._get_branch(
                client=client,
                project_id=project_id,
                branch=base_branch_name,
            )
            try:
                await self._get_branch(
                    client=client,
                    project_id=project_id,
                    branch=branch,
                )
            except GitLabApiError as e:
                if e.status_code != 404:
                    raise
                await self._create_branch(
                    client=client,
                    project_id=project_id,
                    branch=branch,
                    ref=base_branch_name,
                )

            current = await self._read_files_with_client(
                client=client,
                url=url,
                ref=branch,
            )
            current_blob_paths = current.blob_paths or frozenset(current.files)
            changed_files = {
                path: content
                for path, content in sorted(files.items())
                if current.files.get(path) != content
            }
            managed_roots = _normalized_roots(delete_missing_paths_under)
            stale_paths = {
                path
                for path in current_blob_paths
                if path not in files and _path_is_under_roots(path, managed_roots)
            }

            pr_url: str | None = None
            pr_number: int | None = None
            pr_reused = False
            if not changed_files and not stale_paths:
                branch_has_commits = await self._branch_has_commits_between(
                    client=client,
                    project_id=project_id,
                    base_branch_name=base_branch_name,
                    branch_name=branch,
                )
                if create_pr and branch_has_commits:
                    pr_url, pr_number, pr_reused = await self._upsert_merge_request(
                        client=client,
                        project_id=project_id,
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

            actions: list[GitLabCommitAction] = []
            for path, content in sorted(changed_files.items()):
                actions.append(
                    {
                        "action": "update" if path in current_blob_paths else "create",
                        "file_path": path,
                        "content": content,
                    }
                )
            for path in sorted(stale_paths):
                actions.append({"action": "delete", "file_path": path})

            commit_payload: GitLabCreateCommitPayload = {
                "branch": branch,
                "commit_message": message,
                "actions": actions,
            }
            commit = await self._gitlab_model(
                client,
                "POST",
                f"/projects/{project_id}/repository/commits",
                model=GitLabCommit,
                json=commit_payload,
            )
            commit_sha = commit.id

            if create_pr:
                pr_url, pr_number, pr_reused = await self._upsert_merge_request(
                    client=client,
                    project_id=project_id,
                    title=message,
                    branch_name=branch,
                    base_branch_name=base_branch_name,
                )

            return CommitInfo(
                status=PushStatus.COMMITTED,
                sha=commit_sha,
                ref=branch,
                base_ref=base_branch_name,
                pr_url=pr_url,
                pr_number=pr_number,
                pr_reused=pr_reused,
                message="Committed workspace sync changes.",
            )

    async def list_commits(
        self,
        *,
        url: GitUrl,
        branch: str = "main",
        limit: int = 10,
    ) -> list[GitCommitInfo]:
        """Return up to ``limit`` most recent commits on ``branch``."""
        async with self._authed_client(url) as client:
            project_id = _gitlab_project_id(url)
            commits_params: GitLabListCommitsParams = {"ref_name": branch}
            raw_commits = await self._gitlab_list(
                client,
                f"/projects/{project_id}/repository/commits",
                model=GitLabCommit,
                params=commits_params,
                limit=limit,
            )
        return [
            GitCommitInfo(
                sha=commit.id,
                message=commit.message or commit.title or "",
                author=commit.author_name or "Unknown",
                author_email=commit.author_email or "",
                date=commit.authored_date
                or commit.committed_date
                or commit.created_at
                or "",
                tags=[],
            )
            for commit in raw_commits
        ]

    async def list_branches(
        self,
        *,
        url: GitUrl,
        limit: int = 100,
    ) -> list[GitBranchInfo]:
        """Return up to ``limit`` branches, flagging the repository default."""
        async with self._authed_client(url) as client:
            return await self._list_branches_with_client(
                client=client,
                url=url,
                limit=limit,
            )

    async def _credentials(self) -> GitLabTokenCredentials:
        """Load organization GitLab token credentials."""
        return await GitLabTokenService(
            session=self.session,
            role=self.role,
        ).get_gitlab_token_credentials()

    def _client(self, credentials: GitLabTokenCredentials) -> httpx.AsyncClient:
        """Build an authenticated GitLab REST API client."""
        return httpx.AsyncClient(
            base_url=f"{credentials.base_url}/api/v4",
            headers={"PRIVATE-TOKEN": credentials.token.get_secret_value()},
            timeout=30.0,
        )

    @asynccontextmanager
    async def _authed_client(self, url: GitUrl) -> AsyncIterator[httpx.AsyncClient]:
        """Yield an authenticated client, asserting the repo lives on the instance.

        The API host comes from the organization GitLab connection's ``base_url``
        while the project path comes from the workspace ``git_repo_url``. Guarding
        that they reference the same host prevents a workspace URL from silently
        resolving against a different GitLab instance than the one the token
        authenticates to.

        Dedicated SSH host aliases (for example ``ssh.gitlab.example.test`` or
        ``altssh.gitlab.com``) are allowed as subdomains of the configured
        instance host, and ports are ignored so custom SSH ports still match.
        """
        credentials = await self._credentials()
        expected_host = _gitlab_instance_host(credentials.base_url)
        repo_host = _gitlab_instance_host(url.host)
        if expected_host and not (
            repo_host == expected_host or repo_host.endswith(f".{expected_host}")
        ):
            raise TracecatValidationError(
                f"Workspace repository host '{url.host}' does not match the "
                f"configured GitLab instance '{expected_host}'. Update the "
                "workspace Git URL or the organization GitLab connection so they "
                "reference the same host."
            )
        async with self._client(credentials) as client:
            yield client

    async def _read_files_with_client(
        self,
        *,
        client: httpx.AsyncClient,
        url: GitUrl,
        ref: str,
    ) -> VcsTreeSnapshot:
        """Read workspace sync files with an existing GitLab client."""
        project_id = _gitlab_project_id(url)
        commit = await self._gitlab_model(
            client,
            "GET",
            f"/projects/{project_id}/repository/commits/{quote(ref, safe='')}",
            model=GitLabCommit,
        )
        commit_sha = commit.id

        tree_params: GitLabTreeParams = {"recursive": "true", "ref": commit_sha}
        tree_entries = await self._gitlab_list(
            client,
            f"/projects/{project_id}/repository/tree",
            model=GitLabTreeEntry,
            params=tree_params,
        )
        blob_shas = {
            entry.path: entry.id for entry in tree_entries if entry.type == "blob"
        }
        blob_semaphore = asyncio.Semaphore(_GITLAB_BLOB_CONCURRENCY)

        async def fetch_text(path: str) -> str | None:
            """Fetch a blob raw payload and decode it as UTF-8."""
            async with blob_semaphore:
                response = await self._gitlab_response(
                    client,
                    "GET",
                    f"/projects/{project_id}/repository/blobs/{quote(blob_shas[path], safe='')}/raw",
                )
            try:
                return response.content.decode("utf-8")
            except UnicodeDecodeError:
                return None

        files = await self._select_snapshot_files(
            blob_paths=list(blob_shas),
            fetch_text=fetch_text,
        )
        return VcsTreeSnapshot(
            commit_sha=commit_sha,
            tree_sha=None,
            files=files,
            blob_paths=frozenset(blob_shas),
        )

    async def _list_branches_with_client(
        self,
        *,
        client: httpx.AsyncClient,
        url: GitUrl,
        limit: int | None,
    ) -> list[GitBranchInfo]:
        """List branches using an existing GitLab client."""
        project_id = _gitlab_project_id(url)
        branch_path = f"/projects/{project_id}/repository/branches"
        raw_branches = await self._gitlab_branches_including_default(
            client,
            branch_path,
            limit=limit,
        )
        return [
            GitBranchInfo(name=branch.name, is_default=branch.default)
            for branch in raw_branches
        ]

    async def _get_branch(
        self,
        *,
        client: httpx.AsyncClient,
        project_id: str,
        branch: str,
    ) -> GitLabBranch:
        """Return a GitLab branch object or raise a structured API error."""
        return await self._gitlab_model(
            client,
            "GET",
            f"/projects/{project_id}/repository/branches/{quote(branch, safe='')}",
            model=GitLabBranch,
        )

    async def _get_project(
        self,
        *,
        client: httpx.AsyncClient,
        project_id: str,
    ) -> GitLabProject:
        """Return a GitLab project object or raise a structured API error."""
        return await self._gitlab_model(
            client,
            "GET",
            f"/projects/{project_id}",
            model=GitLabProject,
        )

    async def _create_branch(
        self,
        *,
        client: httpx.AsyncClient,
        project_id: str,
        branch: str,
        ref: str,
    ) -> None:
        """Create ``branch`` from ``ref``."""
        create_branch_params: GitLabCreateBranchParams = {"branch": branch, "ref": ref}
        await self._gitlab_json(
            client,
            "POST",
            f"/projects/{project_id}/repository/branches",
            params=create_branch_params,
        )

    async def _gitlab_branches_including_default(
        self,
        client: httpx.AsyncClient,
        path: str,
        *,
        limit: int | None,
    ) -> list[GitLabBranch]:
        """Collect branches, continuing past ``limit`` until the default is known."""
        if limit is not None and limit <= 0:
            return []

        branches: list[GitLabBranch] = []
        default_branch: GitLabBranch | None = None
        page = 1
        while True:
            response = await self._gitlab_response(
                client,
                "GET",
                path,
                params={"page": page, "per_page": _GITLAB_PAGE_SIZE},
            )
            data = self._json(response)
            if not isinstance(data, list):
                raise GitLabError("GitLab list response was invalid")
            try:
                page_branches = [GitLabBranch.model_validate(item) for item in data]
            except PydanticValidationError as e:
                raise GitLabError("GitLab list response was invalid") from e

            if default_branch is None:
                default_branch = next(
                    (branch for branch in page_branches if branch.default),
                    None,
                )

            if limit is None:
                branches.extend(page_branches)
            else:
                remaining = limit - len(branches)
                if remaining > 0:
                    branches.extend(page_branches[:remaining])

            next_page = response.headers.get("x-next-page")
            if limit is not None and len(branches) >= limit and default_branch:
                break
            if not next_page:
                break
            try:
                page = int(next_page)
            except ValueError as e:
                raise GitLabError("GitLab pagination response was invalid") from e

        if (
            limit is not None
            and default_branch is not None
            and all(branch.name != default_branch.name for branch in branches)
        ):
            branches = [*branches[: limit - 1], default_branch]
        return branches

    async def _upsert_merge_request(
        self,
        *,
        client: httpx.AsyncClient,
        project_id: str,
        title: str,
        branch_name: str,
        base_branch_name: str,
    ) -> tuple[str | None, int | None, bool]:
        """Reuse or open the sync merge request for ``branch_name``."""
        list_mr_params: GitLabListMergeRequestsParams = {
            "state": "opened",
            "source_branch": branch_name,
            "target_branch": base_branch_name,
        }
        existing = await self._gitlab_list(
            client,
            f"/projects/{project_id}/merge_requests",
            model=GitLabMergeRequest,
            params=list_mr_params,
            limit=1,
        )
        if existing:
            mr = existing[0]
            return mr.web_url, mr.iid, True

        create_mr_payload: GitLabCreateMergeRequestPayload = {
            "source_branch": branch_name,
            "target_branch": base_branch_name,
            "title": title,
            "description": await self._sync_request_body(),
        }
        mr = await self._gitlab_model(
            client,
            "POST",
            f"/projects/{project_id}/merge_requests",
            model=GitLabMergeRequest,
            json=create_mr_payload,
        )
        return mr.web_url, mr.iid, False

    async def _branch_has_commits_between(
        self,
        *,
        client: httpx.AsyncClient,
        project_id: str,
        base_branch_name: str,
        branch_name: str,
    ) -> bool:
        """Return whether ``branch_name`` is ahead of ``base_branch_name``."""
        compare_params: GitLabCompareParams = {
            "from": base_branch_name,
            "to": branch_name,
        }
        comparison = await self._gitlab_model(
            client,
            "GET",
            f"/projects/{project_id}/repository/compare",
            model=GitLabCompareResult,
            params=compare_params,
        )
        return bool(comparison.commits)

    async def _gitlab_list[ModelT: BaseModel](
        self,
        client: httpx.AsyncClient,
        path: str,
        *,
        model: type[ModelT],
        params: Mapping[str, Any] | None = None,
        limit: int | None = None,
    ) -> list[ModelT]:
        """Collect and validate a paginated GitLab list response."""
        results: list[ModelT] = []
        page = 1
        while True:
            page_params = {
                **(params or {}),
                "page": page,
                "per_page": _GITLAB_PAGE_SIZE,
            }
            response = await self._gitlab_response(
                client,
                "GET",
                path,
                params=page_params,
            )
            data = self._json(response)
            if not isinstance(data, list):
                raise GitLabError("GitLab list response was invalid")
            try:
                results.extend(model.model_validate(item) for item in data)
            except PydanticValidationError as e:
                raise GitLabError("GitLab list response was invalid") from e
            if limit is not None and len(results) >= limit:
                return results[:limit]
            next_page = response.headers.get("x-next-page")
            if not next_page:
                return results
            try:
                page = int(next_page)
            except ValueError as e:
                raise GitLabError("GitLab pagination response was invalid") from e

    async def _gitlab_model[ModelT: BaseModel](
        self,
        client: httpx.AsyncClient,
        method: str,
        path: str,
        *,
        model: type[ModelT],
        **kwargs: Any,
    ) -> ModelT:
        """Return a GitLab JSON response validated against ``model``."""
        data = await self._gitlab_json(client, method, path, **kwargs)
        if not isinstance(data, dict):
            raise GitLabError(f"GitLab {model.__name__} response was invalid")
        try:
            return model.model_validate(data)
        except PydanticValidationError as e:
            raise GitLabError(f"GitLab {model.__name__} response was invalid") from e

    async def _gitlab_json(
        self,
        client: httpx.AsyncClient,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> Any:
        """Return a decoded GitLab JSON response."""
        response = await self._gitlab_response(client, method, path, **kwargs)
        if response.status_code == 204 or not response.content:
            return None
        return self._json(response)

    async def _gitlab_response(
        self,
        client: httpx.AsyncClient,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> httpx.Response:
        """Return a GitLab response, raising structured API errors."""
        try:
            response = await client.request(method, path, **kwargs)
        except httpx.HTTPError as e:
            raise GitLabError(f"GitLab API request failed: {str(e)}") from e
        if response.status_code >= 400:
            message = _gitlab_error_message(response)
            raise GitLabApiError(
                f"GitLab API error: {response.status_code} - {message}",
                status_code=response.status_code,
                detail={"status_code": response.status_code, "message": message},
            )
        return response

    def _json(self, response: httpx.Response) -> Any:
        """Decode a GitLab JSON response."""
        try:
            return response.json()
        except ValueError as e:
            raise GitLabError("GitLab response was not valid JSON") from e


def _gitlab_instance_host(value: str) -> str:
    """Return the lowercased hostname for a GitLab URL or bare host."""
    parsed = urlparse(value if "://" in value else f"//{value}")
    return (parsed.hostname or "").lower()


def _gitlab_project_id(url: GitUrl) -> str:
    """Return GitLab's URL-encoded ``namespace/project`` identifier."""
    return quote(f"{url.org}/{url.repo}", safe="")


def _gitlab_error_message(response: httpx.Response) -> str:
    """Extract a readable GitLab error without branching on error strings."""
    try:
        payload = response.json()
    except ValueError:
        return response.text[:500]
    if isinstance(payload, dict):
        message = payload.get("message") or payload.get("error_description")
        if isinstance(message, str):
            return message
        if isinstance(message, dict):
            return str(message)
    return str(payload)
