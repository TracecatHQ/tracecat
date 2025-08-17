"""Workflow synchronization functionality for Tracecat."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime

import yaml
from github.GithubException import GithubException
from github.InputGitAuthor import InputGitAuthor
from tracecat_ee.workflow.store import GitWorkflowStore

from tracecat.dsl.common import DSLInput
from tracecat.git.utils import GitUrl, resolve_git_ref
from tracecat.identifiers import WorkspaceID
from tracecat.logger import logger
from tracecat.service import BaseWorkspaceService
from tracecat.ssh import git_env_context
from tracecat.sync import CommitInfo, PullOptions, PushOptions
from tracecat.types.auth import Role
from tracecat.vcs.github.app import GitHubAppError, GitHubAppService
from tracecat.workflow.management.definitions import WorkflowDefinitionsService
from tracecat.workflow.store.models import WorkflowSource

# Type alias for YAML fetching function
FetchYaml = Callable[[WorkflowSource], Awaitable[str]]


# This will be used in the pull flow
async def upsert_workflow_definitions(
    sources: list[WorkflowSource],
    *,
    fetch_yaml: FetchYaml,
    commit_sha: str,
    workspace_id: WorkspaceID,
    repo_url: str | None = None,
) -> None:
    """Upsert workflow definitions from external sources.

    For each workflow source, fetches the YAML content, parses it to DSL format,
    and upserts it into the WorkflowDefinition table with Git metadata.

    Args:
        sources: List of workflow sources to process.
        fetch_yaml: Function to fetch YAML content given path and SHA.
        commit_sha: Git commit SHA for this sync operation.
        workspace_id: Workspace ID for the definitions.

    Raises:
        Exception: If YAML parsing or database operations fail.
    """
    logger.info(
        "Starting workflow definitions upsert",
        source_count=len(sources),
        commit_sha=commit_sha,
        workspace_id=workspace_id,
    )

    # Create a temporary role for the service
    role = Role(
        type="service",
        service_id="tracecat-service",
        workspace_id=workspace_id,
    )

    async with WorkflowDefinitionsService.with_session(role=role) as service:
        for source in sources:
            try:
                logger.debug(
                    "Processing workflow source",
                    path=source.path,
                    workflow_id=source.id,
                    sha=source.sha,
                )

                # Fetch YAML content
                yaml_content = await fetch_yaml(source)

                # Parse YAML to DSL
                workflow_data = yaml.safe_load(yaml_content)
                dsl = DSLInput(**workflow_data)

                logger.debug(
                    "Parsed workflow DSL",
                    title=dsl.title,
                    workflow_id=source.id,
                )

                # workflow_id is already a WorkflowID instance
                workflow_id = source.id

                # Create workflow definition with Git metadata
                # Note: We extend the base create method to include Git metadata
                # The exact implementation depends on how WorkflowDefinition schema supports metadata
                defn = await service.create_workflow_definition(
                    workflow_id=workflow_id,
                    dsl=dsl,
                    commit=False,  # We'll commit in batch
                )

                # Add Git metadata to the definition
                # Set git metadata fields on the definition
                defn.origin = "git"
                defn.repo_path = source.path
                defn.commit_sha = commit_sha
                if repo_url is not None:
                    defn.repo_url = repo_url

                logger.info(
                    "Created workflow definition",
                    workflow_id=source.id,
                    version=defn.version,
                    path=source.path,
                )

            except Exception as e:
                logger.error(
                    "Failed to process workflow source",
                    path=source.path,
                    workflow_id=source.id,
                    error=str(e),
                )
                raise

        # Commit all changes at once
        await service.session.commit()

        logger.info(
            "Successfully upserted workflow definitions",
            source_count=len(sources),
            commit_sha=commit_sha,
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
    ) -> list[DSLInput]:
        """Pull workflow definitions from a Git repository.

        Args:
            url: Git repository URL with optional ref
            options: Pull options for depth, paths, etc.

        Returns:
            List of DSLInput workflow definitions
        """

        options = options or PullOptions()

        # 1. Set up Git environment and resolve ref
        async with git_env_context(
            git_url=url, session=self.session, role=self.role
        ) as env:
            commit_sha = await resolve_git_ref(url.to_url(), ref=url.ref, env=env)

            # 2. Initialize GitWorkflowStore
            if GitWorkflowStore is None:
                raise ImportError(
                    "GitWorkflowStore not available - enterprise features required"
                )
            store = GitWorkflowStore(
                repo_url=url.to_url(),
                commit_sha=commit_sha,
                env=env,
            )

            # 3. List and fetch workflow sources
            sources = await store.list_sources()
            dsls: list[DSLInput] = []

            for source in sources:
                # Apply path filtering if specified
                if options.paths and not any(
                    source.path.startswith(p) for p in options.paths
                ):
                    continue

                # Fetch and parse YAML
                yaml_content = await store.fetch_content(source)
                workflow_data = yaml.safe_load(yaml_content)
                dsls.append(DSLInput(**workflow_data))

            logger.info(
                "Successfully pulled workflows",
                count=len(dsls),
                commit_sha=commit_sha,
            )

            return dsls

    async def push(
        self,
        *,
        objects: Sequence[DSLInput],
        url: GitUrl,
        options: PushOptions,
    ) -> CommitInfo:
        """Push workflow definitions using GitHub App API operations.

        Args:
            objects: DSLInput workflow definitions to push
            url: Git repository URL with target branch
            options: Push options including commit message and PR flag

        Returns:
            CommitInfo with commit SHA and branch/PR details
        """
        if len(objects) == 0:
            raise ValueError("No workflow objects to push")

        gh_svc = GitHubAppService(session=self.session, role=self.role)

        # Use new PyGithub-based method that handles installation resolution automatically
        gh = await gh_svc.get_github_client_for_repo(url)

        try:
            repo = await asyncio.to_thread(gh.get_repo, f"{url.org}/{url.repo}")

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
                repo=f"{url.org}/{url.repo}",
            )

            await asyncio.to_thread(
                repo.create_git_ref,
                ref=f"refs/heads/{branch_name}",
                sha=base_branch.commit.sha,
            )

            # Create/update workflow files via API
            for dsl in objects:
                # Generate filename from workflow title (sanitized)
                title_slug = dsl.title.lower().replace(" ", "-")
                # Remove special characters and limit length
                title_slug = "".join(c for c in title_slug if c.isalnum() or c in "-_")[
                    :50
                ]
                filename = f"{title_slug}.yaml"
                file_path = f"workflows/{filename}"

                # Serialize to YAML
                yaml_content = yaml.dump(
                    dsl.model_dump(exclude_none=True, exclude_unset=True)
                )

                # Set author info
                author_name = options.author.name if options.author else "Tracecat"
                author_email = (
                    options.author.email if options.author else "noreply@tracecat.com"
                )
                git_author = InputGitAuthor(name=author_name, email=author_email)

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
                        author=git_author,
                        committer=git_author,
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
                            author=git_author,
                            committer=git_author,
                        )
                        logger.debug(
                            "Created workflow file via API",
                            path=file_path,
                            branch=branch_name,
                        )
                    else:
                        raise

            # Get the latest commit SHA from the branch
            branch = await asyncio.to_thread(repo.get_branch, branch_name)
            commit_sha = branch.commit.sha

            # Create PR if requested
            pr_url = None
            if options.create_pr:
                try:
                    pr = await asyncio.to_thread(
                        repo.create_pull,
                        title=options.message,
                        body=f"Automated workflow sync from Tracecat workspace {self.workspace_id}",
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
                count=len(objects),
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
                repo=f"{url.org}/{url.repo}",
            )
            raise GitHubAppError(f"GitHub API error: {e.status} - {e.data}") from e
        finally:
            gh.close()
