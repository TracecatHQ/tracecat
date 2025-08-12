"""Enterprise Edition Git workflow synchronization orchestrator."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.db.locks import derive_lock_key, pg_advisory_lock
from tracecat.db.schemas import WorkflowDefinition, WorkflowRepoState
from tracecat.git import parse_git_url, resolve_git_ref
from tracecat.identifiers import WorkspaceID
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.logger import logger
from tracecat.settings.service import get_setting_cached
from tracecat.ssh import git_env_context
from tracecat.store.core import WorkflowSource
from tracecat.store.sync import upsert_workflow_definitions
from tracecat.types.auth import Role
from tracecat.workflow.management.management import WorkflowsManagementService
from tracecat_ee.store.git_store import GitWorkflowStore


async def sync_repo_workflows(
    *,
    session: AsyncSession,
    workspace_id: WorkspaceID,
    repo_url: str,
    ref: str | None = None,
    role: Role | None = None,
) -> dict[str, int | str]:
    """Synchronize workflows from a Git repository with mirror semantics.

    This function provides idempotent sync with advisory locking to prevent
    concurrent syncs for the same workspace+repo combination.

    Args:
        session: Database session
        workspace_id: Target workspace UUID
        repo_url: Git repository URL
        ref: Git reference to sync (branch/tag/SHA), defaults to HEAD
        role: User role for authorization and SSH context

    Returns:
        Dict with sync status and counts:
        - status: "unchanged" | "synced"
        - commit_sha: The resolved commit SHA
        - created: Number of workflows created (if synced)
        - updated: Number of workflows updated (if synced)
        - deleted: Number of workflows deleted (if synced)

    Raises:
        ValueError: If repo_url is invalid or not in allowed domains
        RuntimeError: If Git operations fail
    """
    # Parse and validate Git URL
    allowed_domains = await get_setting_cached("REGISTRY__ALLOWED_REPO_DOMAINS")
    allowed_domains_set = set(allowed_domains) if allowed_domains else None
    git_url = parse_git_url(repo_url, allowed_domains=allowed_domains_set)

    # Acquire advisory lock for this workspace+repo combination
    lock_key = derive_lock_key(workspace_id, repo_url)

    async with pg_advisory_lock(session, lock_key):
        logger.info("Acquired lock for sync", workspace=workspace_id, repo=repo_url)

        async with git_env_context(git_url=git_url, session=session, role=role) as env:
            # Resolve ref to commit SHA
            commit_sha = await resolve_git_ref(repo_url, ref=ref, env=env)
            logger.info("Resolved ref", ref=ref, commit_sha=commit_sha)

            # Check if we already synced this commit
            repo_state = await _get_or_create_repo_state(
                session, workspace_id, repo_url
            )

            if repo_state.last_synced_sha == commit_sha:
                logger.info("Repository already synced", commit_sha=commit_sha)
                return {
                    "status": "unchanged",
                    "commit_sha": commit_sha,
                }

            # Initialize Git store and list sources
            git_store = GitWorkflowStore(
                repo_url=repo_url,
                commit_sha=commit_sha,
                env=env,
            )

            sources = list(await git_store.list_sources())
            logger.info("Found workflow sources", count=len(sources))

            # Perform mirror delete: remove workflows that no longer exist in repo
            deleted_count = await _mirror_delete_workflows(
                session, workspace_id, repo_url, sources
            )

            # Track original workflow count for metrics
            original_count = await _count_existing_workflows(
                session, workspace_id, repo_url
            )

            # Upsert workflow definitions from Git sources
            await upsert_workflow_definitions(
                sources,
                fetch_yaml=git_store.fetch_yaml,
                commit_sha=commit_sha,
                workspace_id=workspace_id,
                repo_url=repo_url,
            )

            # Update repo state
            repo_state.last_synced_sha = commit_sha
            repo_state.last_synced_at = datetime.now()
            session.add(repo_state)
            await session.commit()

            # Calculate metrics
            final_count = await _count_existing_workflows(
                session, workspace_id, repo_url
            )
            created_count = max(0, final_count - original_count + deleted_count)
            updated_count = len(sources) - created_count

            logger.info(
                f"Sync completed: {created_count} created, {updated_count} updated, "
                f"{deleted_count} deleted"
            )

            return {
                "status": "synced",
                "commit_sha": commit_sha,
                "created": created_count,
                "updated": updated_count,
                "deleted": deleted_count,
            }


async def _get_or_create_repo_state(
    session: AsyncSession, workspace_id: WorkspaceID, repo_url: str
) -> WorkflowRepoState:
    """Get or create WorkflowRepoState for the given workspace+repo."""
    statement = select(WorkflowRepoState).where(
        WorkflowRepoState.workspace_id == workspace_id,
        WorkflowRepoState.repo_url == repo_url,
    )

    result = await session.execute(statement)
    repo_state = result.scalar_one_or_none()

    if repo_state is None:
        repo_state = WorkflowRepoState(
            workspace_id=workspace_id,
            repo_url=repo_url,
            last_synced_sha=None,
            last_synced_at=None,
        )
        session.add(repo_state)
        await session.flush()  # Get the ID

    return repo_state


async def _mirror_delete_workflows(
    session: AsyncSession,
    workspace_id: WorkspaceID,
    repo_url: str,
    current_sources: Sequence[WorkflowSource],
) -> int:
    """Delete workflows that exist in DB but not in current Git sources.

    Returns:
        Number of workflows deleted
    """
    # Get current source paths
    current_paths = {source.path for source in current_sources}

    # Find existing workflow definitions from this repo
    statement = select(WorkflowDefinition).where(
        WorkflowDefinition.owner_id == workspace_id,
        WorkflowDefinition.origin == "git",
        WorkflowDefinition.repo_url == repo_url,
    )

    result = await session.execute(statement)
    existing_definitions = result.scalars().all()

    # Group by workflow_id and get latest version per workflow
    latest_definitions: dict[str, WorkflowDefinition] = {}
    for defn in existing_definitions:
        workflow_id = str(defn.workflow_id)
        if (
            workflow_id not in latest_definitions
            or defn.version > latest_definitions[workflow_id].version
        ):
            latest_definitions[workflow_id] = defn

    # Find workflows to delete (paths that exist in DB but not in current sources)
    deleted_count = 0
    role = Role(
        type="service",
        service_id="tracecat-service",
        workspace_id=workspace_id,
    )
    workflows_service = WorkflowsManagementService.with_session(role=role)

    for defn in latest_definitions.values():
        if defn.repo_path not in current_paths:
            logger.info(
                "Deleting workflow",
                workflow_id=defn.workflow_id,
                path=defn.repo_path,
            )
            try:
                async with workflows_service as service:
                    await service.delete_workflow(WorkflowUUID(defn.workflow_id))
                deleted_count += 1
            except Exception as e:
                logger.error(f"Failed to delete workflow {defn.workflow_id}: {e}")

    return deleted_count


async def _count_existing_workflows(
    session: AsyncSession, workspace_id: WorkspaceID, repo_url: str
) -> int:
    """Count existing workflows from this repo in the workspace."""
    # Count distinct workflows (not definitions) from this repo
    statement = (
        select(WorkflowDefinition.workflow_id)
        .where(
            WorkflowDefinition.owner_id == workspace_id,
            WorkflowDefinition.origin == "git",
            WorkflowDefinition.repo_url == repo_url,
        )
        .distinct()
    )

    result = await session.execute(statement)
    return len(result.scalars().all())
