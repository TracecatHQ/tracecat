from fastapi import APIRouter, HTTPException, Query, status

from tracecat.auth.dependencies import WorkspaceUserRole
from tracecat.db.dependencies import AsyncDBSession
from tracecat.dsl.common import DSLInput
from tracecat.exceptions import (
    TracecatCredentialsNotFoundError,
    TracecatSettingsError,
)
from tracecat.git.utils import parse_git_url
from tracecat.identifiers.workflow import AnyWorkflowIDPath
from tracecat.logger import logger
from tracecat.registry.repositories.schemas import GitCommitInfo
from tracecat.sync import PullOptions, PullResult
from tracecat.vcs.github.app import GitHubAppError
from tracecat.workflow.management.definitions import WorkflowDefinitionsService
from tracecat.workflow.store.schemas import WorkflowDslPublish, WorkflowSyncPullRequest
from tracecat.workflow.store.service import WorkflowStoreService
from tracecat.workflow.store.sync import WorkflowSyncService
from tracecat.workspaces.service import WorkspaceService

router = APIRouter(prefix="/workflows", tags=["workflows"])


@router.post("/{workflow_id}/publish", status_code=status.HTTP_204_NO_CONTENT)
async def publish_workflow(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    workflow_id: AnyWorkflowIDPath,
    params: WorkflowDslPublish,
):
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
        await store_svc.publish_workflow_dsl(
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
async def list_workflow_commits(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    branch: str = Query(
        default="main",
        description="Branch name to fetch commits from",
        min_length=1,
        max_length=255,
    ),
    limit: int = Query(
        default=10,
        description="Maximum number of commits to return",
        ge=1,
        le=100,
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

        # Initialize workflow sync service
        sync_service = WorkflowSyncService(session=session, role=role)

        # Fetch commits using GitHub App API
        commits = await sync_service.list_commits(
            url=git_url,
            branch=branch,
            limit=limit,
        )

        return commits

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
        logger.exception(
            f"Error fetching commits from repository: {repository_url}", exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch repository commits",
        ) from e


@router.post("/sync/pull", response_model=PullResult)
async def pull_workflows(
    role: WorkspaceUserRole,
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

        # Initialize workflow sync service
        sync_service = WorkflowSyncService(session=session, role=role)

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
