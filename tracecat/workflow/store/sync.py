"""Workflow synchronization functionality for Tracecat."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any

import yaml
from github.GithubException import GithubException

if TYPE_CHECKING:
    from github.ContentFile import ContentFile
from pydantic import ValidationError

from tracecat.db.models import User
from tracecat.exceptions import TracecatNotFoundError
from tracecat.git.utils import GitUrl
from tracecat.logger import logger
from tracecat.registry.repositories.schemas import GitCommitInfo
from tracecat.service import BaseWorkspaceService
from tracecat.sync import (
    CommitInfo,
    PullDiagnostic,
    PullOptions,
    PullResult,
    PushObject,
    PushOptions,
)
from tracecat.vcs.github.app import GitHubAppError, GitHubAppService
from tracecat.workflow.store.import_service import WorkflowImportService
from tracecat.workflow.store.schemas import RemoteWorkflowDefinition
from tracecat.workspaces.service import WorkspaceService


def _extract_github_error_message(data: Any) -> str:
    """Extract a human-readable message from a GitHub API error payload."""
    if isinstance(data, dict):
        for key in ("message", "error"):
            value = data.get(key)
            if isinstance(value, str) and value:
                return value
    if isinstance(data, str) and data:
        return data
    return "Unknown GitHub API error"


def _format_github_api_error(*, status: int, data: Any) -> str:
    """Format GitHub API errors without embedding raw dict payloads."""
    message = _extract_github_error_message(data)
    return f"GitHub API error ({status}): {message}"


def _format_push_error(
    *,
    status: int,
    data: Any,
    repo_name: str,
    branch_name: str | None,
) -> str:
    """Format publish errors with branch-aware guidance."""
    github_message = _extract_github_error_message(data)
    if status == 404 and github_message.casefold() == "branch not found":
        branch = branch_name or "<unknown>"
        return (
            f"Cannot publish workflow because branch '{branch}' was not found in "
            f"repository '{repo_name}'. Update workspace Git settings to use an "
            "existing branch."
        )
    return (
        f"Cannot publish workflow to '{repo_name}'. "
        f"{_format_github_api_error(status=status, data=data)}"
    )


# NOTE: Internal service called by higher level services, shouldn't use directly
class WorkflowSyncService(BaseWorkspaceService):
    """Git synchronization service for workflow definitions.

    Implements the SyncService protocol for DSLInput workflow models,
    providing pull/push operations with Git repositories.
    """

    service_name = "workflow_sync"

    async def pull(
        self,
        *,
        url: GitUrl,
        options: PullOptions | None = None,
    ) -> PullResult:
        """Pull workflow definitions from a Git repository at specific commit SHA.

        This implementation provides atomic guarantees - either all workflows
        are imported successfully or none are.

        Args:
            url: Git repository URL
            options: Pull options including commit SHA and conflict strategy

        Returns:
            PullResult with success status and diagnostics

        Raises:
            GitHubAppError: If GitHub authentication or API errors occur
        """
        if not options or not options.commit_sha:
            return PullResult(
                success=False,
                commit_sha="",
                workflows_found=0,
                workflows_imported=0,
                diagnostics=[
                    PullDiagnostic(
                        workflow_path="",
                        workflow_title=None,
                        error_type="validation",
                        message="commit_sha is required in pull options",
                        details={},
                    )
                ],
                message="commit_sha is required",
            )

        try:
            # 1. Fetch repository content at specific commit SHA
            repo_content = await self._fetch_repository_content(url, options.commit_sha)

            # 2. Parse workflow definitions
            (
                remote_workflows,
                parse_diagnostics,
            ) = await self._parse_workflow_definitions(repo_content)

            if parse_diagnostics:
                return PullResult(
                    success=False,
                    commit_sha=options.commit_sha,
                    workflows_found=len(repo_content),
                    workflows_imported=0,
                    diagnostics=parse_diagnostics,
                    message=f"Failed to parse {len(parse_diagnostics)} workflow definitions",
                )

            # 3. Import workflows atomically
            if options.dry_run:
                # For dry run, skip import and return validation-only result
                return PullResult(
                    success=True,
                    commit_sha=options.commit_sha,
                    workflows_found=len(remote_workflows),
                    workflows_imported=0,
                    diagnostics=[],
                    message="Dry run completed - workflows validated but not imported",
                )

            import_service = WorkflowImportService(session=self.session, role=self.role)

            return await import_service.import_workflows_atomic(
                remote_workflows=remote_workflows,
                commit_sha=options.commit_sha,
            )

        except GitHubAppError as e:
            logger.error("GitHub API error during pull", error=str(e))
            return PullResult(
                success=False,
                commit_sha=options.commit_sha or "",
                workflows_found=0,
                workflows_imported=0,
                diagnostics=[
                    PullDiagnostic(
                        workflow_path="",
                        workflow_title=None,
                        error_type="github",
                        message=f"GitHub API error: {str(e)}",
                        details={"error": str(e)},
                    )
                ],
                message="GitHub API error",
            )
        except Exception as e:
            logger.error("Unexpected error during pull", error=str(e), exc_info=True)
            return PullResult(
                success=False,
                commit_sha=options.commit_sha or "",
                workflows_found=0,
                workflows_imported=0,
                diagnostics=[
                    PullDiagnostic(
                        workflow_path="",
                        workflow_title=None,
                        error_type="system",
                        message=f"Unexpected error: {str(e)}",
                        details={"error": str(e)},
                    )
                ],
                message="System error",
            )

    async def _fetch_repository_content(
        self, url: GitUrl, commit_sha: str
    ) -> dict[str, str]:
        """Fetch workflow definitions from repository at specific commit SHA.

        Args:
            url: Git repository URL
            commit_sha: Specific commit SHA to fetch from

        Returns:
            Dictionary mapping file paths to file content

        Raises:
            GitHubAppError: If GitHub API errors occur
        """
        gh_svc = GitHubAppService(session=self.session, role=self.role)
        gh = await gh_svc.get_github_client_for_repo(url)

        try:
            repo = await asyncio.to_thread(gh.get_repo, f"{url.org}/{url.repo}")

            # Get the workflows directory at the specific commit
            try:
                workflows_contents = await asyncio.to_thread(
                    repo.get_contents, "workflows", ref=commit_sha
                )

                if not isinstance(workflows_contents, list):
                    # workflows is a file, not a directory
                    return {}

                content_map = {}

                for item in workflows_contents:
                    item: ContentFile = item  # type hint for GitHub API object
                    # Look for workflow directories
                    if item.type == "dir":
                        # Get definition.yml from each workflow directory
                        definition_path = f"{item.path}/definition.yml"
                        try:
                            definition_file = await asyncio.to_thread(
                                repo.get_contents, definition_path, ref=commit_sha
                            )

                            if not isinstance(definition_file, list) and hasattr(
                                definition_file, "content"
                            ):
                                # Decode base64 content
                                content = base64.b64decode(
                                    definition_file.content
                                ).decode("utf-8")
                                content_map[definition_path] = content
                        except GithubException as e:
                            if e.status != 404:  # Ignore missing definition.yml files
                                logger.warning(
                                    "Failed to fetch workflow definition",
                                    path=definition_path,
                                    status=e.status,
                                    github_message=_extract_github_error_message(
                                        e.data
                                    ),
                                )

                return content_map

            except GithubException as e:
                if e.status == 404:
                    # No workflows directory found
                    return {}
                raise

        except GithubException as e:
            raise GitHubAppError(
                _format_github_api_error(status=e.status, data=e.data),
                detail={
                    "status": e.status,
                    "data": e.data,
                    "repo": f"{url.org}/{url.repo}",
                },
            ) from e
        finally:
            gh.close()

    async def _parse_workflow_definitions(
        self, content_map: dict[str, str]
    ) -> tuple[list[RemoteWorkflowDefinition], list[PullDiagnostic]]:
        """Parse workflow definitions from file contents.

        Args:
            content_map: Dictionary mapping file paths to content

        Returns:
            Tuple of (remote_workflows, diagnostics)
        """
        remote_workflows: list[RemoteWorkflowDefinition] = []
        diagnostics: list[PullDiagnostic] = []

        for file_path, content in content_map.items():
            yaml_data: dict[str, Any] | None = None
            try:
                # Parse YAML content
                yaml_data = yaml.safe_load(content)
                if not yaml_data:
                    diagnostics.append(
                        PullDiagnostic(
                            workflow_path=file_path,
                            workflow_title=None,
                            error_type="parse",
                            message="Empty or invalid YAML file",
                            details={},
                        )
                    )
                    continue

                # Convert to RemoteWorkflowDefinition
                remote_workflow = RemoteWorkflowDefinition.model_validate(yaml_data)
                remote_workflows.append(remote_workflow)

            except yaml.YAMLError as e:
                diagnostics.append(
                    PullDiagnostic(
                        workflow_path=file_path,
                        workflow_title=None,
                        error_type="parse",
                        message=f"YAML parsing error: {str(e)}",
                        details={"yaml_error": str(e)},
                    )
                )
            except ValidationError as e:
                diagnostics.append(
                    PullDiagnostic(
                        workflow_path=file_path,
                        workflow_title=yaml_data.get("definition", {}).get("title")
                        if isinstance(yaml_data, dict)
                        else None,
                        error_type="validation",
                        message=f"Validation error: {str(e)}",
                        details={"validation_errors": e.errors()},
                    )
                )
            except Exception as e:
                diagnostics.append(
                    PullDiagnostic(
                        workflow_path=file_path,
                        workflow_title=None,
                        error_type="parse",
                        message=f"Unexpected parsing error: {str(e)}",
                        details={"error": str(e)},
                    )
                )

        return remote_workflows, diagnostics

    async def push(
        self,
        *,
        objects: Sequence[PushObject[RemoteWorkflowDefinition]],
        url: GitUrl,
        options: PushOptions,
    ) -> CommitInfo:
        """Push workflow definitions using GitHub App API operations.

        Args:
            objects: PushObjects containing workflow definitions and target paths
            url: Git repository URL with target branch
            options: Push options including commit message and PR flag

        Returns:
            CommitInfo with commit SHA and branch/PR details
        """
        if len(objects) != 1:
            raise ValueError("We only support pushing one workflow object at a time")

        [obj] = objects

        gh_svc = GitHubAppService(session=self.session, role=self.role)

        # Use new PyGithub-based method that handles installation resolution automatically
        gh = await gh_svc.get_github_client_for_repo(url)
        repo_name = f"{url.org}/{url.repo}"
        base_branch_name: str | None = None

        try:
            repo = await asyncio.to_thread(gh.get_repo, repo_name)

            # Get base branch
            base_branch_name = url.ref or repo.default_branch
            base_branch = await asyncio.to_thread(repo.get_branch, base_branch_name)

            # Create feature branch
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            branch_name = f"tracecat-sync-{timestamp}"

            logger.info(
                "Creating branch via GitHub API",
                branch=branch_name,
                base_branch=base_branch_name,
                repo=repo_name,
            )

            await asyncio.to_thread(
                repo.create_git_ref,
                ref=f"refs/heads/{branch_name}",
                sha=base_branch.commit.sha,
            )

            # Create/update workflow files via API
            file_path = obj.path_str

            yaml_content = yaml.dump(
                obj.data.model_dump(mode="json", exclude_none=True, exclude_unset=True),
                sort_keys=False,
            )

            # NOTE: We intentionally omit author/committer parameters to enable
            # GitHub's automatic commit signing for GitHub Apps. Per GitHub docs:
            # "Signature verification for bots will only work if the request
            # contains no custom author information, custom committer information"
            # https://docs.github.com/en/authentication/managing-commit-signature-verification/about-commit-signature-verification

            try:
                # Try to get existing file to update it
                contents = await asyncio.to_thread(
                    repo.get_contents, file_path, ref=branch_name
                )
                # get_contents returns ContentFile for files, or list for directories
                if isinstance(contents, list):
                    raise GithubException(404, {"message": "Not a file"}, {})

                await asyncio.to_thread(
                    repo.update_file,
                    path=contents.path,
                    message=options.message,
                    content=yaml_content,
                    sha=contents.sha,
                    branch=branch_name,
                )
                logger.debug(
                    "Updated workflow file via API",
                    path=file_path,
                    branch=branch_name,
                )
            except GithubException as e:
                if e.status == 404:
                    # File doesn't exist, create it
                    await asyncio.to_thread(
                        repo.create_file,
                        path=file_path,
                        message=options.message,
                        content=yaml_content,
                        branch=branch_name,
                    )
                    logger.debug(
                        "Created workflow file via API",
                        path=file_path,
                        branch=branch_name,
                    )

            # Get the latest commit SHA from the branch
            branch = await asyncio.to_thread(repo.get_branch, branch_name)
            commit_sha = branch.commit.sha

            # Create PR if requested
            pr_url = None
            if options.create_pr:
                try:
                    ws_svc = WorkspaceService(session=self.session, role=self.role)
                    workspace = await ws_svc.get_workspace(self.workspace_id)
                    if not workspace:
                        raise TracecatNotFoundError("Workspace not found")

                    try:
                        title = obj.data.definition.title
                        description = obj.data.definition.description
                    except ValueError:
                        title = "<An error occurred while determining the title>"
                        description = (
                            "<An error occurred while determining the description>"
                        )

                    try:
                        current_user = await self.session.get(User, self.role.user_id)
                    except Exception:
                        current_user = None

                    published_by = current_user.email if current_user else "<unknown>"

                    pr = await asyncio.to_thread(
                        repo.create_pull,
                        title=options.message,
                        body=(
                            f"Automated workflow sync from Tracecat\n\n"
                            f"**Workspace:** {workspace.name}\n"
                            f"**Published by:** {published_by}\n"
                            f"**Workflow Title:** {title}\n"
                            f"**Workflow Description:** {description}"
                        ),
                        head=branch_name,
                        base=base_branch_name,
                    )
                    pr_url = pr.html_url

                    logger.info(
                        "Created PR via GitHub API",
                        pr_number=pr.number,
                        pr_url=pr_url,
                    )
                except GithubException as e:
                    logger.error(
                        "Failed to create PR via GitHub API",
                        error=str(e),
                        branch=branch_name,
                    )
                    # Don't fail the entire operation if PR creation fails

            logger.info(
                "Successfully pushed workflows via GitHub API",
                count=1,
                branch=branch_name,
                commit_sha=commit_sha,
                pr_created=pr_url is not None,
            )

            return CommitInfo(
                sha=commit_sha,
                ref=branch_name,
            )

        except GithubException as e:
            logger.error(
                "GitHub API error during push",
                status=e.status,
                data=e.data,
                repo=repo_name,
                branch=base_branch_name,
                github_message=_extract_github_error_message(e.data),
            )
            raise GitHubAppError(
                _format_push_error(
                    status=e.status,
                    data=e.data,
                    repo_name=repo_name,
                    branch_name=base_branch_name or url.ref,
                ),
                detail={
                    "status": e.status,
                    "data": e.data,
                    "repo": repo_name,
                    "branch": base_branch_name or url.ref,
                },
            ) from e
        finally:
            gh.close()

    async def list_commits(
        self,
        *,
        url: GitUrl,
        branch: str = "main",
        limit: int = 10,
    ) -> list[GitCommitInfo]:
        """List commits from a Git repository using GitHub App API.

        Args:
            url: Git repository URL
            branch: Branch name to fetch commits from
            limit: Maximum number of commits to return

        Returns:
            List of GitCommitInfo objects with commit details

        Raises:
            GitHubAppError: If GitHub authentication or API errors occur
        """
        try:
            # Get authenticated GitHub client
            gh_svc = GitHubAppService(session=self.session, role=self.role)
            gh = await gh_svc.get_github_client_for_repo(url)

            try:
                # Get repository object
                repo = await asyncio.to_thread(gh.get_repo, f"{url.org}/{url.repo}")

                # Fetch commits using PyGithub
                commits_paginated = await asyncio.to_thread(
                    repo.get_commits, sha=branch
                )

                # Get all tags to build SHA-to-tags mapping
                tags_paginated = await asyncio.to_thread(repo.get_tags)
                sha_to_tags: dict[str, list[str]] = {}

                # Build mapping of commit SHA to tag names in thread to avoid blocking
                def build_tag_mapping():
                    result_map = {}
                    for tag in tags_paginated:
                        tag_sha = tag.commit.sha
                        if tag_sha not in result_map:
                            result_map[tag_sha] = []
                        result_map[tag_sha].append(tag.name)
                    return result_map

                sha_to_tags = await asyncio.to_thread(build_tag_mapping)

                # Convert to GitCommitInfo objects
                commits = []
                count = 0
                for commit in commits_paginated:
                    if count >= limit:
                        break

                    # Get tags for this commit SHA, default to empty list
                    tags = sha_to_tags.get(commit.sha, [])

                    commits.append(
                        GitCommitInfo(
                            sha=commit.sha,
                            message=commit.commit.message,
                            author=commit.commit.author.name or "Unknown",
                            author_email=commit.commit.author.email or "",
                            date=commit.commit.author.date.isoformat(),
                            tags=tags,
                        )
                    )
                    count += 1

                return commits

            finally:
                gh.close()

        except GithubException as e:
            logger.error(
                "GitHub API error during commit listing",
                status=e.status,
                data=e.data,
                repo=f"{url.org}/{url.repo}",
                branch=branch,
            )
            raise GitHubAppError(
                _format_github_api_error(status=e.status, data=e.data),
                detail={
                    "status": e.status,
                    "data": e.data,
                    "repo": f"{url.org}/{url.repo}",
                    "branch": branch,
                },
            ) from e
