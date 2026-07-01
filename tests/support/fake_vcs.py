from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from tracecat.exceptions import TracecatValidationError
from tracecat.git.types import GitUrl
from tracecat.registry.repositories.schemas import GitBranchInfo, GitCommitInfo
from tracecat.sync import CommitInfo, PushStatus
from tracecat.workspace_sync.enums import VcsProvider
from tracecat.workspace_sync.transport import VcsSyncTransport, VcsTreeSnapshot


@dataclass
class _FakeCommit:
    sha: str
    tree_sha: str
    message: str
    branch: str
    files: dict[str, str]
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class FakeVcsServer:
    """In-memory VCS server for workspace sync acceptance tests."""

    def __init__(self, *, default_branch: str = "main") -> None:
        self.default_branch = default_branch
        self._repos: dict[str, _FakeRepo] = {}

    def transport_factory(
        self,
        provider: VcsProvider,
        *,
        session: Any,
        role: Any,
    ) -> VcsSyncTransport:
        del session, role
        if provider not in {VcsProvider.GITHUB, VcsProvider.GITLAB}:
            raise TracecatValidationError(f"Unsupported fake VCS provider: {provider}")
        return FakeVcsTransport(server=self)

    def repo_files(self, url: GitUrl, *, ref: str | None = None) -> dict[str, str]:
        repo = self._repo(url)
        return repo.files_at_ref(ref or self.default_branch)

    def _repo(self, url: GitUrl) -> _FakeRepo:
        key = f"{url.host}/{url.org}/{url.repo}"
        repo = self._repos.get(key)
        if repo is None:
            repo = _FakeRepo(default_branch=self.default_branch)
            self._repos[key] = repo
        return repo


class FakeVcsTransport:
    def __init__(self, *, server: FakeVcsServer) -> None:
        self._server = server

    async def read_files(self, *, url: GitUrl, ref: str) -> VcsTreeSnapshot:
        repo = self._server._repo(url)
        commit = repo.commit_at_ref(ref)
        return VcsTreeSnapshot(
            commit_sha=commit.sha,
            tree_sha=commit.tree_sha,
            files=dict(commit.files),
            blob_paths=frozenset(commit.files),
        )

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
        del create_pr
        repo = self._server._repo(url)
        return repo.write_files(
            files=files,
            message=message,
            branch=branch,
            base_branch=pr_base_branch or url.ref or self._server.default_branch,
            delete_missing_paths_under=delete_missing_paths_under,
        )

    async def list_commits(
        self,
        *,
        url: GitUrl,
        branch: str = "main",
        limit: int = 10,
    ) -> list[GitCommitInfo]:
        repo = self._server._repo(url)
        return [
            GitCommitInfo(
                sha=commit.sha,
                message=commit.message,
                author="Fake VCS",
                author_email="fake-vcs@example.test",
                date=commit.created_at.isoformat(),
                tags=[],
            )
            for commit in repo.commits_for_branch(branch, limit=limit)
        ]

    async def list_branches(
        self,
        *,
        url: GitUrl,
        limit: int = 100,
    ) -> list[GitBranchInfo]:
        repo = self._server._repo(url)
        return [
            GitBranchInfo(
                name=branch,
                is_default=branch == repo.default_branch,
            )
            for branch in repo.branch_names(limit=limit)
        ]


class _FakeRepo:
    def __init__(self, *, default_branch: str) -> None:
        self.default_branch = default_branch
        self._counter = 0
        self._commits: dict[str, _FakeCommit] = {}
        self._branches: dict[str, str] = {}
        initial = self._new_commit(
            branch=default_branch,
            message="Initial fake VCS commit",
            files={},
        )
        self._branches[default_branch] = initial.sha

    def write_files(
        self,
        *,
        files: dict[str, str],
        message: str,
        branch: str,
        base_branch: str,
        delete_missing_paths_under: Sequence[str] = (),
    ) -> CommitInfo:
        if not files:
            raise ValueError("At least one file is required for workspace sync export")
        message = message.strip()
        if not message:
            raise ValueError("A non-empty commit message is required")

        if branch not in self._branches:
            self._branches[branch] = self.commit_at_ref(base_branch).sha

        current = self.commit_at_ref(branch)
        changed = {
            path: content
            for path, content in files.items()
            if current.files.get(path) != content
        }
        managed_roots = _normalized_roots(delete_missing_paths_under)
        deleted = {
            path
            for path in current.files
            if path not in files and _path_is_under_roots(path, managed_roots)
        }
        if not changed and not deleted:
            return CommitInfo(
                status=PushStatus.NO_OP,
                sha=None,
                ref=branch,
                base_ref=base_branch,
                message="No changes detected; nothing to commit.",
            )

        next_files = {
            path: content
            for path, content in current.files.items()
            if path not in deleted
        }
        next_files.update(changed)
        commit = self._new_commit(
            branch=branch,
            message=message,
            files=next_files,
        )
        self._branches[branch] = commit.sha
        return CommitInfo(
            status=PushStatus.COMMITTED,
            sha=commit.sha,
            ref=branch,
            base_ref=base_branch,
            message=f"Committed {len(changed) + len(deleted)} file(s).",
        )

    def commit_at_ref(self, ref: str) -> _FakeCommit:
        sha = self._branches.get(ref, ref)
        try:
            return self._commits[sha]
        except KeyError as e:
            raise KeyError(f"Unknown fake VCS ref: {ref}") from e

    def files_at_ref(self, ref: str) -> dict[str, str]:
        return dict(self.commit_at_ref(ref).files)

    def commits_for_branch(self, branch: str, *, limit: int) -> list[_FakeCommit]:
        branch_commit = self.commit_at_ref(branch)
        commits = [
            commit
            for commit in self._commits.values()
            if commit.branch == branch and commit.sha <= branch_commit.sha
        ]
        return sorted(commits, key=lambda commit: commit.sha, reverse=True)[:limit]

    def branch_names(self, *, limit: int) -> list[str]:
        return sorted(self._branches)[:limit]

    def _new_commit(
        self,
        *,
        branch: str,
        message: str,
        files: dict[str, str],
    ) -> _FakeCommit:
        self._counter += 1
        sha = f"{self._counter:040x}"
        commit = _FakeCommit(
            sha=sha,
            tree_sha=f"tree-{sha}",
            message=message,
            branch=branch,
            files=dict(files),
        )
        self._commits[sha] = commit
        return commit


def _normalized_roots(roots: Sequence[str]) -> tuple[str, ...]:
    return tuple(root.strip("/") for root in roots if root.strip("/"))


def _path_is_under_roots(path: str, roots: Sequence[str]) -> bool:
    return any(path == root or path.startswith(f"{root}/") for root in roots)
