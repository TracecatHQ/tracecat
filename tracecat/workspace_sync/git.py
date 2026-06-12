"""GitHub-backed repo operations for workspace sync."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from typing import Any

from github.GithubException import GithubException
from github.InputGitTreeElement import InputGitTreeElement

from tracecat.db.models import User
from tracecat.exceptions import TracecatNotFoundError
from tracecat.git.types import GitUrl
from tracecat.registry.repositories.schemas import GitBranchInfo, GitCommitInfo
from tracecat.service import BaseWorkspaceService
from tracecat.sync import CommitInfo, PushStatus
from tracecat.vcs.github.app import GitHubAppError, GitHubAppService
from tracecat.workspaces.service import WorkspaceService


@dataclass(frozen=True)
class GitTreeSnapshot:
    commit_sha: str
    tree_sha: str | None
    files: dict[str, str]


class WorkspaceGitHubSyncService(BaseWorkspaceService):
    """GitHub App transport for workspace sync."""

    service_name = "workspace_github_sync"

    async def read_files(
        self,
        *,
        url: GitUrl,
        ref: str,
    ) -> GitTreeSnapshot:
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
            files: dict[str, str] = {}
            for item in tree.tree:
                if item.type != "blob" or not item.path:
                    continue
                content_file = await asyncio.to_thread(
                    repo.get_contents,
                    item.path,
                    ref=commit.sha,
                )
                if isinstance(content_file, list):
                    continue
                files[item.path] = base64.b64decode(content_file.content).decode(
                    "utf-8"
                )
            return GitTreeSnapshot(
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
    ) -> CommitInfo:
        if not files:
            raise ValueError("At least one file is required for workspace sync export")
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

            changed_files: dict[str, str] = {}
            for path, content in files.items():
                existing_content: str | None = None
                try:
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
                    changed_files[path] = content

            pr_url: str | None = None
            pr_number: int | None = None
            pr_reused = False
            if not changed_files:
                if create_pr:
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
            elements = []
            for path, content in sorted(changed_files.items()):
                blob = await asyncio.to_thread(repo.create_git_blob, content, "utf-8")
                elements.append(
                    InputGitTreeElement(
                        path=path,
                        mode="100644",
                        type="blob",
                        sha=blob.sha,
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

            if create_pr:
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

    async def list_commits(
        self,
        *,
        url: GitUrl,
        branch: str = "main",
        limit: int = 10,
    ) -> list[GitCommitInfo]:
        gh_svc = GitHubAppService(session=self.session, role=self.role)
        gh = await gh_svc.get_github_client_for_repo(url)
        try:
            repo = await asyncio.to_thread(gh.get_repo, f"{url.org}/{url.repo}")
            commits_paginated = await asyncio.to_thread(repo.get_commits, sha=branch)
            commits: list[GitCommitInfo] = []
            for index, commit in enumerate(commits_paginated):
                if index >= limit:
                    break
                commits.append(
                    GitCommitInfo(
                        sha=commit.sha,
                        message=commit.commit.message,
                        author=commit.commit.author.name or "Unknown",
                        author_email=commit.commit.author.email or "",
                        date=commit.commit.author.date.isoformat(),
                        tags=[],
                    )
                )
            return commits
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
        gh_svc = GitHubAppService(session=self.session, role=self.role)
        gh = await gh_svc.get_github_client_for_repo(url)
        try:
            repo = await asyncio.to_thread(gh.get_repo, f"{url.org}/{url.repo}")
            branches_paginated = await asyncio.to_thread(repo.get_branches)
            branches: list[GitBranchInfo] = []
            for index, branch_obj in enumerate(branches_paginated):
                if index >= limit:
                    break
                branches.append(
                    GitBranchInfo(
                        name=branch_obj.name,
                        is_default=branch_obj.name == repo.default_branch,
                    )
                )
            return branches
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
        def _first_open_pull_request() -> Any | None:
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
            session=self.session, role=self.role
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
