"""Workflow synchronization functionality for Tracecat."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from pathlib import Path

import aiofiles
import yaml
from tracecat_ee.workflow.store import GitWorkflowStore

from tracecat.dsl.common import DSLInput
from tracecat.git import GitUrl, resolve_git_ref, run_git
from tracecat.identifiers import WorkspaceID
from tracecat.logger import logger
from tracecat.service import BaseWorkspaceService
from tracecat.ssh import git_env_context
from tracecat.sync import CommitInfo, PullOptions, PushOptions
from tracecat.types.auth import Role
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
        """Push workflow definitions to a Git repository.

        Creates a feature branch and optionally a pull request.

        Args:
            objects: DSLInput workflow definitions to push
            url: Git repository URL with target branch
            options: Push options including commit message and PR flag

        Returns:
            CommitInfo with commit SHA and branch/PR details
        """
        # Validate inputs
        if not options.message:
            raise ValueError("Commit message is required")

        if not objects:
            raise ValueError("No workflow objects to push")

        # 1. Clone repository to temp directory
        async with (
            aiofiles.tempfile.TemporaryDirectory() as tmp_dir,
            git_env_context(git_url=url, session=self.session, role=self.role) as env,
        ):
            work_dir = Path(tmp_dir)
            # Clone the repository
            await self._clone_repo(url, work_dir, env)

            # 2. Create feature branch
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            branch_name = f"tracecat-sync-{timestamp}"
            await self._create_branch(branch_name, work_dir, env)

            # 3. Write workflow YAML files
            workflows_dir = work_dir / "workflows"
            workflows_dir.mkdir(exist_ok=True)

            for dsl in objects:
                # Generate filename from workflow title (sanitized)
                title_slug = dsl.title.lower().replace(" ", "-")
                # Remove special characters and limit length
                title_slug = "".join(c for c in title_slug if c.isalnum() or c in "-_")[
                    :50
                ]
                filename = f"{title_slug}.yaml"
                filepath = workflows_dir / filename

                # Serialize to YAML
                yaml_content = yaml.dump(
                    dsl.model_dump(exclude_none=True, exclude_unset=True)
                )
                async with aiofiles.open(filepath, "w", encoding="utf-8") as f:
                    await f.write(yaml_content)

            # 4. Commit changes
            await self._commit_changes(work_dir, options, env)

            # 5. Push to remote
            await self._push_branch(branch_name, work_dir, env)

            # 6. Get commit SHA
            commit_sha = await self._get_commit_sha(work_dir, env)

            # 7. Create PR if requested
            pr_url = None
            if options.create_pr:
                pr_url = await self._create_pull_request(
                    url, branch_name, options.message, work_dir, env
                )

            logger.info(
                "Successfully pushed workflows",
                count=len(objects),
                branch=branch_name,
                commit_sha=commit_sha,
                pr_created=pr_url is not None,
            )

            return CommitInfo(
                sha=commit_sha,
                ref=branch_name,
            )

    async def _clone_repo(
        self, url: GitUrl, work_dir: Path, env: dict[str, str]
    ) -> None:
        """Clone repository to working directory."""
        returncode, _, stderr = await run_git(
            ["git", "clone", "--depth", "1", url.to_url(), str(work_dir)],
            env=env,
            timeout=60.0,
        )
        if returncode != 0:
            raise RuntimeError(f"Failed to clone repository: {stderr}")

    async def _create_branch(
        self, branch: str, work_dir: Path, env: dict[str, str]
    ) -> None:
        """Create and checkout new branch."""
        returncode, _, stderr = await run_git(
            ["git", "checkout", "-b", branch],
            env=env,
            cwd=str(work_dir),
        )
        if returncode != 0:
            raise RuntimeError(f"Failed to create branch {branch}: {stderr}")

    async def _commit_changes(
        self, work_dir: Path, options: PushOptions, env: dict[str, str]
    ) -> None:
        """Stage and commit all changes."""
        # Add all changes
        returncode, _, stderr = await run_git(
            ["git", "add", "-A"], env=env, cwd=str(work_dir)
        )
        if returncode != 0:
            raise RuntimeError(f"Failed to stage changes: {stderr}")

        # Set author from options or defaults
        author_name = options.author.name if options.author else "Tracecat"
        author_email = (
            options.author.email if options.author else "noreply@tracecat.com"
        )

        # Create env with Git identity
        commit_env = dict(env)
        commit_env["GIT_AUTHOR_NAME"] = author_name
        commit_env["GIT_AUTHOR_EMAIL"] = author_email
        commit_env["GIT_COMMITTER_NAME"] = author_name
        commit_env["GIT_COMMITTER_EMAIL"] = author_email

        # Build commit args with -c flags
        commit_args = [
            "git",
            "-c",
            f"user.name={author_name}",
            "-c",
            f"user.email={author_email}",
            "commit",
            "-m",
            options.message,
        ]
        if options.author:
            commit_args.extend(["--author", f"{author_name} <{author_email}>"])

        returncode, _, stderr = await run_git(
            commit_args, env=commit_env, cwd=str(work_dir)
        )
        if returncode != 0:
            raise RuntimeError(f"Failed to commit changes: {stderr}")

    async def _push_branch(
        self, branch: str, work_dir: Path, env: dict[str, str]
    ) -> None:
        """Push branch to remote."""
        returncode, _, stderr = await run_git(
            ["git", "push", "origin", branch],
            env=env,
            cwd=str(work_dir),
        )
        if returncode != 0:
            raise RuntimeError(f"Failed to push branch {branch}: {stderr}")

    async def _get_commit_sha(self, work_dir: Path, env: dict[str, str]) -> str:
        """Get current commit SHA."""
        returncode, stdout, stderr = await run_git(
            ["git", "rev-parse", "HEAD"],
            env=env,
            cwd=str(work_dir),
        )
        if returncode != 0:
            raise RuntimeError(f"Failed to get commit SHA: {stderr}")
        return stdout.strip()

    async def _create_pull_request(
        self, url: GitUrl, branch: str, title: str, work_dir: Path, env: dict[str, str]
    ) -> str | None:
        """Create pull request using GitHub CLI."""
        try:
            returncode, stdout, stderr = await run_git(
                [
                    "gh",
                    "pr",
                    "new",
                    "--title",
                    title,
                    "--body",
                    f"Automated workflow sync from Tracecat workspace {self.workspace_id}",
                    "--base",
                    url.ref or "main",
                    "--head",
                    branch,
                ],
                env=env,
                cwd=str(work_dir),
            )
            if returncode != 0:
                logger.error(f"Failed to create PR: {stderr}")
                raise RuntimeError(f"Failed to create PR: {stderr}")
            # Extract PR URL from output
            if returncode == 0:
                return stdout.strip()
        except Exception as e:
            logger.warning(f"Could not create PR automatically: {e}")

        return None
