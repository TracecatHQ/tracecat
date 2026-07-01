from fastapi import APIRouter, HTTPException, Query, status

from tracecat import config
from tracecat.auth.dependencies import WorkspaceActorRouteRole
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.dsl.common import DSLInput
from tracecat.exceptions import (
    ScopeDeniedError,
    TracecatCredentialsNotFoundError,
    TracecatNotFoundError,
    TracecatSettingsError,
    TracecatValidationError,
)
from tracecat.identifiers.workflow import AnyWorkflowIDPath
from tracecat.logger import logger
from tracecat.registry.repositories.schemas import GitBranchInfo, GitCommitInfo
from tracecat.sync import PullOptions, PullResult
from tracecat.vcs.github.app import GitHubAppError, GitHubAppService
from tracecat.vcs.github.schemas import GitHubAppRepository
from tracecat.vcs.gitlab.app import GitLabError
from tracecat.workflow.management.definitions import WorkflowDefinitionsService
from tracecat.workflow.store.schemas import (
    WorkflowDslPublish,
    WorkflowDslPublishResult,
    WorkflowSyncPullRequest,
)
from tracecat.workflow.store.service import WorkflowStoreService
from tracecat.workspace_sync.schemas import (
    WorkspaceSyncExportPreview,
    WorkspaceSyncExportPreviewRequest,
    WorkspaceSyncExportRequest,
    WorkspaceSyncExportResult,
)
from tracecat.workspace_sync.service import WorkspaceSyncService

router = APIRouter(prefix="/workflows", tags=["workflows"])


@router.post(
    "/{workflow_id}/publish",
    response_model=WorkflowDslPublishResult,
)
@require_scope("workflow:update")
@require_scope("workflow:sync", "workspace_sync:sync", require_all=False)
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


@router.get("/sync/repositories", response_model=list[GitHubAppRepository])
@require_scope("workspace:update")
async def list_workflow_repositories(
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
) -> list[GitHubAppRepository]:
    """List repositories granted to the configured GitHub App installation."""
    if not role.workspace_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workspace ID is required",
        )

    try:
        github_service = GitHubAppService(session=session, role=role)
        return await github_service.list_accessible_repositories()
    except GitHubAppError as e:
        logger.error(
            "GitHub App error listing accessible repositories",
            error=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unable to list repositories: {str(e)}",
        ) from e


@router.get("/sync/commits", response_model=list[GitCommitInfo])
@require_scope("workflow:sync", "workspace_sync:sync", require_all=False)
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
    """Get commit list for the configured workspace repository.

    Returns a list of commits from the repository configured in workspace settings,
    suitable for use in workflow pull operations.
    """
    if not role.workspace_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workspace ID is required",
        )

    try:
        sync_service = await WorkspaceSyncService.for_workspace(
            session=session, role=role
        )
        return await sync_service.list_commits(
            branch=branch,
            limit=limit,
        )
    except HTTPException:
        raise
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    except (
        TracecatSettingsError,
        TracecatValidationError,
        GitHubAppError,
        GitLabError,
    ) as e:
        logger.error("Git sync error fetching commits", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except Exception as e:
        logger.error(
            "Error fetching commits from repository",
            error=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch repository commits",
        ) from e


@router.get("/sync/branches", response_model=list[GitBranchInfo])
@require_scope("workflow:sync", "workspace_sync:sync", require_all=False)
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
    """Get branch list for the configured workspace repository."""
    if not role.workspace_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workspace ID is required",
        )

    try:
        sync_service = await WorkspaceSyncService.for_workspace(
            session=session, role=role
        )
        return await sync_service.list_branches(limit=limit)
    except HTTPException:
        raise
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    except (
        TracecatSettingsError,
        TracecatValidationError,
        GitHubAppError,
        GitLabError,
    ) as e:
        logger.error("Git sync error fetching branches", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except Exception as e:
        logger.error(
            "Error fetching branches from repository",
            error=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch repository branches",
        ) from e


@router.post("/sync/export", response_model=WorkspaceSyncExportResult)
@require_scope("workspace_sync:sync")
async def export_workspace_sync(
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    params: WorkspaceSyncExportRequest,
) -> WorkspaceSyncExportResult:
    """Export workspace workflow specs to a Git branch and optional PR."""
    if not role.workspace_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workspace ID is required",
        )
    try:
        sync_service = await WorkspaceSyncService.for_workspace(
            session=session, role=role
        )
        return await sync_service.export_workspace(params)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    except (
        TracecatSettingsError,
        TracecatValidationError,
        GitHubAppError,
        GitLabError,
    ) as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e


@router.post("/sync/export/preview", response_model=WorkspaceSyncExportPreview)
@require_scope("workspace_sync:sync")
async def preview_export_workspace_sync(
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    params: WorkspaceSyncExportPreviewRequest,
) -> WorkspaceSyncExportPreview:
    """Project which resources an export would commit, without writing to Git."""
    if not role.workspace_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workspace ID is required",
        )
    try:
        sync_service = await WorkspaceSyncService.for_workspace(
            session=session, role=role
        )
        return await sync_service.preview_export_workspace(params)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    except (
        TracecatSettingsError,
        TracecatValidationError,
        GitHubAppError,
        GitLabError,
    ) as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e


@router.post("/sync/pull", response_model=PullResult)
@require_scope("workflow:sync", "workspace_sync:sync", require_all=False)
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

    try:
        pull_options = PullOptions(
            commit_sha=params.commit_sha,
            dry_run=params.dry_run,
        )
        sync_service = await WorkspaceSyncService.for_workspace(
            session=session, role=role
        )
        return await sync_service.pull(
            options=pull_options,
            sync_schedules=params.sync_schedules,
        )
    except ValueError as e:
        logger.error(
            f"Invalid pull request parameters: {params.model_dump()}", exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid parameters: {str(e)}",
        ) from e
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    except (
        TracecatSettingsError,
        TracecatValidationError,
        GitHubAppError,
        GitLabError,
    ) as e:
        logger.error("Git sync error during workflow pull", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except ScopeDeniedError:
        raise
    except Exception as e:
        logger.error(
            "Error pulling workflows from repository",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to pull workflows from repository",
        ) from e
