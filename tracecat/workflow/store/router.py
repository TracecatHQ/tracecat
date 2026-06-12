import uuid

from fastapi import APIRouter, HTTPException, Query, status

from tracecat import config
from tracecat.auth.dependencies import WorkspaceActorRouteRole
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.dsl.common import DSLInput
from tracecat.exceptions import (
    TracecatCredentialsNotFoundError,
    TracecatNotFoundError,
    TracecatSettingsError,
    TracecatValidationError,
)
from tracecat.git.utils import parse_git_url
from tracecat.identifiers.workflow import AnyWorkflowIDPath
from tracecat.logger import logger
from tracecat.registry.repositories.schemas import GitBranchInfo, GitCommitInfo
from tracecat.sync import PullOptions, PullResult
from tracecat.vcs.github.app import GitHubAppError
from tracecat.workflow.management.definitions import WorkflowDefinitionsService
from tracecat.workflow.store.schemas import (
    WorkflowDslPublish,
    WorkflowDslPublishResult,
    WorkflowSyncPullRequest,
    validate_short_branch_name,
)
from tracecat.workflow.store.service import WorkflowStoreService
from tracecat.workspace_sync.schemas import (
    ChangeSetCreate,
    ChangeSetExport,
    ChangeSetRead,
    WorkspaceSyncExportResult,
    WorkspaceSyncPendingChanges,
    WorkspaceSyncStatus,
)
from tracecat.workspace_sync.service import WorkspaceGitSyncService
from tracecat.workspaces.service import WorkspaceService

router = APIRouter(prefix="/workflows", tags=["workflows"])


@router.post(
    "/{workflow_id}/publish",
    response_model=WorkflowDslPublishResult,
)
@require_scope("workflow:update", "workflow:sync")
async def publish_workflow(
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    workflow_id: AnyWorkflowIDPath,
    params: WorkflowDslPublish,
) -> WorkflowDslPublishResult:
    if role.workspace_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Workspace ID is required"
        )
    defn_svc = WorkflowDefinitionsService(session=session)
    defn = await defn_svc.get_definition_by_workflow_id(workflow_id)
    if not defn:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow definition not found",
        )
    # Load workflow relationship after initial load
    await session.refresh(defn, ["workflow"])
    dsl = DSLInput.model_validate(defn.content)
    store_svc = WorkflowStoreService(session=session)
    try:
        return await store_svc.publish_workflow_dsl(
            workflow_id=workflow_id,
            dsl=dsl,
            params=params,
            workflow=defn.workflow,
        )
    except TracecatSettingsError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except TracecatValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except TracecatCredentialsNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except GitHubAppError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e


@router.get("/sync/commits", response_model=list[GitCommitInfo])
@require_scope("workflow:sync")
async def list_workflow_commits(
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    branch: str = Query(
        default="main",
        description="Branch name to fetch commits from",
        min_length=1,
        max_length=255,
    ),
    limit: int = Query(
        default=config.TRACECAT__LIMIT_COMMITS_DEFAULT,
        description="Maximum number of commits to return",
        ge=config.TRACECAT__LIMIT_MIN,
        le=config.TRACECAT__LIMIT_CURSOR_MAX,
    ),
) -> list[GitCommitInfo]:
    """Get commit list for workflow repository via GitHub App.

    Returns a list of commits from the repository configured in workspace settings,
    suitable for use in workflow pull operations.
    """
    if not role.workspace_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workspace ID is required",
        )

    repository_url = None  # Initialize to avoid UnboundLocalError in exception handlers
    try:
        # Get workspace and repository URL from settings
        workspace_service = WorkspaceService(session=session, role=role)
        workspace = await workspace_service.get_workspace(role.workspace_id)

        if not workspace:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Workspace not found",
            )

        repository_url = workspace.settings.get("git_repo_url")

        if not repository_url:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Git repository URL not configured in workspace settings",
            )

        # Parse and validate Git URL
        git_url = parse_git_url(repository_url)

        # Initialize workspace sync service
        sync_service = WorkspaceGitSyncService(session=session, role=role)

        # Fetch commits using GitHub App API
        commits = await sync_service.list_commits(
            url=git_url,
            branch=branch,
            limit=limit,
        )

        return commits

    except HTTPException:
        raise
    except ValueError as e:
        logger.error(f"Invalid repository URL: {repository_url}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid repository URL: {str(e)}",
        ) from e
    except GitHubAppError as e:
        logger.error(
            f"GitHub App error accessing repository: {repository_url}", exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unable to access repository: {str(e)}",
        ) from e
    except Exception as e:
        logger.error(
            "Error fetching commits from repository",
            repository_url=repository_url,
            error=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch repository commits",
        ) from e


@router.get("/sync/branches", response_model=list[GitBranchInfo])
@require_scope("workflow:sync")
async def list_workflow_branches(
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    limit: int = Query(
        default=config.TRACECAT__LIMIT_COMMITS_DEFAULT,
        description="Maximum number of branches to return",
        ge=config.TRACECAT__LIMIT_MIN,
        le=config.TRACECAT__LIMIT_CURSOR_MAX,
    ),
) -> list[GitBranchInfo]:
    """Get branch list for workflow repository via GitHub App."""
    if not role.workspace_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workspace ID is required",
        )

    repository_url = None
    try:
        workspace_service = WorkspaceService(session=session, role=role)
        workspace = await workspace_service.get_workspace(role.workspace_id)

        if not workspace:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Workspace not found",
            )

        repository_url = workspace.settings.get("git_repo_url")

        if not repository_url:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Git repository URL not configured in workspace settings",
            )

        git_url = parse_git_url(repository_url)
        sync_service = WorkspaceGitSyncService(session=session, role=role)
        branches = await sync_service.list_branches(url=git_url, limit=limit)
        return branches
    except HTTPException:
        raise
    except ValueError as e:
        logger.error(f"Invalid repository URL: {repository_url}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid repository URL: {str(e)}",
        ) from e
    except GitHubAppError as e:
        logger.error(
            f"GitHub App error accessing repository: {repository_url}", exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unable to access repository: {str(e)}",
        ) from e
    except Exception as e:
        logger.error(
            "Error fetching branches from repository",
            repository_url=repository_url,
            error=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch repository branches",
        ) from e


@router.get("/sync/status", response_model=WorkspaceSyncStatus)
@require_scope("workflow:sync")
async def get_workspace_sync_status(
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
) -> WorkspaceSyncStatus:
    """Get workspace-level Git sync status for the configured repository."""
    if not role.workspace_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workspace ID is required",
        )
    sync_service = WorkspaceGitSyncService(session=session, role=role)
    try:
        return await sync_service.get_status()
    except TracecatSettingsError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e


@router.get("/sync/pending", response_model=WorkspaceSyncPendingChanges)
@require_scope("workflow:sync")
async def list_workspace_sync_pending_changes(
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
) -> WorkspaceSyncPendingChanges:
    """List local syncable workspace changes pending Git export."""
    if not role.workspace_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workspace ID is required",
        )
    sync_service = WorkspaceGitSyncService(session=session, role=role)
    try:
        return await sync_service.list_pending_changes()
    except TracecatSettingsError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e


@router.get("/sync/changesets", response_model=list[ChangeSetRead])
@require_scope("workflow:sync")
async def list_workspace_sync_changesets(
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    limit: int = Query(default=50, ge=1, le=100),
) -> list[ChangeSetRead]:
    """List workspace sync ChangeSets."""
    if not role.workspace_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workspace ID is required",
        )
    sync_service = WorkspaceGitSyncService(session=session, role=role)
    return await sync_service.list_changesets(limit=limit)


@router.post(
    "/sync/changesets",
    response_model=ChangeSetRead,
    status_code=status.HTTP_201_CREATED,
)
@require_scope("workflow:update", "workflow:sync")
async def create_workspace_sync_changeset(
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    params: ChangeSetCreate,
) -> ChangeSetRead:
    """Create a workspace sync ChangeSet from selected pending resources."""
    if not role.workspace_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workspace ID is required",
        )
    sync_service = WorkspaceGitSyncService(session=session, role=role)
    try:
        return await sync_service.create_changeset(params)
    except (TracecatSettingsError, TracecatValidationError) as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e


@router.get("/sync/changesets/{changeset_id}", response_model=ChangeSetRead)
@require_scope("workflow:sync")
async def get_workspace_sync_changeset(
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    changeset_id: uuid.UUID,
) -> ChangeSetRead:
    """Get a workspace sync ChangeSet."""
    if not role.workspace_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workspace ID is required",
        )
    sync_service = WorkspaceGitSyncService(session=session, role=role)
    try:
        return await sync_service.get_changeset(changeset_id)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e


@router.post(
    "/sync/changesets/{changeset_id}/export",
    response_model=WorkspaceSyncExportResult,
)
@require_scope("workflow:update", "workflow:sync")
async def export_workspace_sync_changeset(
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    changeset_id: uuid.UUID,
    params: ChangeSetExport,
) -> WorkspaceSyncExportResult:
    """Export a workspace sync ChangeSet to a Git branch and optional PR."""
    if not role.workspace_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workspace ID is required",
        )
    try:
        validate_short_branch_name(params.branch, field_name="branch")
        if params.pr_base_branch is not None:
            validate_short_branch_name(
                params.pr_base_branch,
                field_name="pr_base_branch",
            )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e

    sync_service = WorkspaceGitSyncService(session=session, role=role)
    try:
        return await sync_service.export_changeset(
            changeset_id=changeset_id,
            params=params,
        )
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    except (TracecatSettingsError, TracecatValidationError, GitHubAppError) as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e


@router.post("/sync/pull", response_model=PullResult)
@require_scope("workflow:update", "workflow:sync")
async def pull_workflows(
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    params: WorkflowSyncPullRequest,
) -> PullResult:
    """Pull workflows from Git repository at specific commit.

    Imports workflow definitions from the specified repository and commit,
    with configurable conflict resolution strategy. Repository URL is retrieved
    from workspace settings.
    """
    if not role.workspace_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workspace ID is required",
        )

    repository_url = None  # Initialize to avoid UnboundLocalError in exception handlers
    try:
        # Get workspace and repository URL from settings
        workspace_service = WorkspaceService(session=session, role=role)
        workspace = await workspace_service.get_workspace(role.workspace_id)

        if not workspace:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Workspace not found",
            )

        repository_url = workspace.settings.get("git_repo_url")

        if not repository_url:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Git repository URL not configured in workspace settings",
            )

        # Parse and validate Git URL
        git_url = parse_git_url(repository_url)

        # Create pull options
        pull_options = PullOptions(
            commit_sha=params.commit_sha,
            dry_run=params.dry_run,
        )

        # Initialize workspace sync service
        sync_service = WorkspaceGitSyncService(session=session, role=role)

        # Perform the pull operation
        return await sync_service.pull(url=git_url, options=pull_options)
    except ValueError as e:
        logger.error(
            f"Invalid pull request parameters: {params.model_dump()}", exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid parameters: {str(e)}",
        ) from e
    except GitHubAppError as e:
        logger.error(
            f"GitHub App error during workflow pull: {repository_url}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unable to access repository: {str(e)}",
        ) from e
    except Exception as e:
        logger.error(
            f"Error pulling workflows from repository: {repository_url}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to pull workflows from repository",
        ) from e
