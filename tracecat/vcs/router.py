"""VCS integration router for organization-level platform features."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status

from tracecat.auth.credentials import RoleACL
from tracecat.auth.dependencies import WorkspaceUserRole
from tracecat.db.dependencies import AsyncDBSession
from tracecat.logger import logger
from tracecat.types.auth import AccessLevel, Role
from tracecat.types.exceptions import TracecatValidationError
from tracecat.vcs.github.app import GitHubAppError, GitHubAppService
from tracecat.vcs.github.flows import (
    handle_manifest_conversion,
    handle_workspace_installation_callback,
)
from tracecat.vcs.github.manifest import generate_github_app_manifest
from tracecat.vcs.models import (
    GitHubAppCredentialsRequest,
    GitHubAppCredentialsStatus,
    GitHubAppInstallRequest,
    GitHubAppManifestResponse,
)

org_router = APIRouter(prefix="/organization/vcs", tags=["vcs", "organization"])


workspace_router = APIRouter(prefix="/workspace/vcs", tags=["vcs", "workspace"])
"""Manage GitHub App for workspaces."""


@workspace_router.post("/install", status_code=status.HTTP_204_NO_CONTENT)
async def install_github_app(
    *,
    session: AsyncDBSession,
    role: WorkspaceUserRole,
    request: GitHubAppInstallRequest,
):
    """Set GitHub App installation ID for workspace."""
    try:
        github_service = GitHubAppService(session=session, role=role)

        # Set the installation ID for the workspace
        if not role.workspace_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Workspace ID is required",
            )

        await github_service.set_workspace_installation(
            workspace_id=role.workspace_id,
            installation_id=request.installation_id,
        )

        logger.info(
            "Set GitHub App installation for workspace",
            workspace_id=role.workspace_id,
            installation_id=request.installation_id,
        )
    except GitHubAppError as e:
        logger.error("Enterprise GitHub App installation failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Enterprise GitHub App installation failed: {e}",
        ) from e
    except Exception as e:
        logger.error(
            "Unexpected error during enterprise GitHub App installation", error=str(e)
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error during enterprise GitHub App installation",
        ) from e


@workspace_router.delete("/install", status_code=status.HTTP_204_NO_CONTENT)
async def uninstall_github_app(
    *,
    session: AsyncDBSession,
    role: WorkspaceUserRole,
):
    """Uninstall GitHub App from workspace."""
    if not role.workspace_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workspace ID is required",
        )
    try:
        github_service = GitHubAppService(session=session, role=role)
        await github_service.uninstall_app(role.workspace_id)

        logger.info(
            "Uninstalled GitHub App",
            workspace_id=role.workspace_id,
        )

    except TracecatValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except Exception as e:
        logger.error("Error uninstalling GitHub App", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error during GitHub App uninstallation",
        ) from e


github_router = APIRouter(prefix="/github", tags=["vcs", "github", "organization"])
"""Manage GitHub App for organization-level features."""


@github_router.get("/manifest", response_model=GitHubAppManifestResponse)
async def get_github_app_manifest(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="no",
        min_access_level=AccessLevel.ADMIN,
    ),
) -> GitHubAppManifestResponse:
    """Generate GitHub App manifest for enterprise installation."""
    try:
        manifest = generate_github_app_manifest()

        instructions = [
            "1. Copy the manifest JSON below",
            "2. Go to GitHub.com and navigate to your organization settings",
            "3. Go to Developer settings > GitHub Apps > New GitHub App",
            "4. Click 'Create GitHub App from manifest'",
            "5. Paste the manifest JSON and click 'Create'",
            "6. GitHub will redirect back to Tracecat to complete the setup automatically",
        ]

        return GitHubAppManifestResponse(
            manifest=manifest,
            instructions=instructions,
        )

    except Exception as e:
        logger.error("Error generating GitHub App manifest", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error generating GitHub App manifest",
        ) from e


@github_router.get("/install")
async def github_app_install_callback(
    *,
    session: AsyncDBSession,
    role: Annotated[
        Role, RoleACL(allow_user=True, allow_service=False, require_workspace="no")
    ],
    code: str | None = Query(
        None, description="Temporary code from GitHub manifest flow"
    ),
    installation_id: int | None = Query(
        None, description="Installation ID from GitHub callback"
    ),
    state: str | None = Query(None, description="CSRF protection state parameter"),
):
    """Handle GitHub App installation flow.

    This endpoint handles two different flows:
    1. Code exchange: When GitHub redirects with a temporary code after manifest submission
    2. Installation callback: When GitHub redirects after app installation
    """
    logger.info(
        "GitHub App installation callback",
        code=code,
        installation_id=installation_id,
        state=state,
    )
    try:
        if code:
            # Code exchange flow: Convert manifest code to app credentials
            return await handle_manifest_conversion(session, role, code, state)
        elif installation_id:
            if not role.workspace_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Workspace ID is required",
                )
            # Installation callback flow: Store installation ID
            return await handle_workspace_installation_callback(
                session, role.workspace_id, role, installation_id, state
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Either 'code' or 'installation_id' parameter is required",
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error handling GitHub App install", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error during GitHub App installation",
        ) from e


@github_router.post("/webhook")
async def github_webhook(
    *,
    session: AsyncDBSession,
    payload: dict[str, Any],
) -> dict[str, str]:
    """Handle GitHub webhook events."""
    try:
        event_type = payload.get("action")
        installation_data = payload.get("installation", {})
        installation_id = installation_data.get("id")

        logger.info(
            "Received GitHub webhook",
            event_type=event_type,
            installation_id=installation_id,
        )

        # Handle installation events
        if event_type in ("created", "deleted") and installation_id:
            logger.info(
                f"GitHub App installation {event_type}",
                installation_id=installation_id,
                account=installation_data.get("account", {}).get("login"),
            )
            # Note: We don't automatically set installation_id here because
            # we cannot reliably correlate webhooks to specific workspaces
            # without additional context. The installation callback flow
            # handles this more reliably.

        # TODO: Process other webhook events
        # - repository access changes
        # - push events for synchronization

        return {"message": "Webhook processed successfully"}

    except Exception as e:
        logger.error("Error processing GitHub webhook", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error processing webhook",
        ) from e


@github_router.post("/credentials", status_code=status.HTTP_201_CREATED)
async def save_github_app_credentials(
    *,
    session: AsyncDBSession,
    role: Annotated[
        Role, RoleACL(allow_user=True, allow_service=False, require_workspace="no")
    ],
    request: GitHubAppCredentialsRequest,
) -> dict[str, str]:
    """Save GitHub App credentials (register new or update existing)."""
    # Organization-level operation, no specific checks needed since this is org VCS

    try:
        github_service = GitHubAppService(session=session, role=role)
        config, was_created = await github_service.save_github_app_credentials(
            app_id=request.app_id,
            private_key_pem=request.private_key,
            webhook_secret=request.webhook_secret,
            client_id=request.client_id,
        )

        action = "created" if was_created else "updated"
        logger.info(
            f"GitHub App credentials {action}",
            app_id=request.app_id,
        )

        return {
            "message": f"GitHub App credentials {action} successfully",
            "action": action,
            "app_id": config.app_id or request.app_id,
        }

    except GitHubAppError as e:
        logger.error("Failed to save GitHub App credentials", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to save GitHub App credentials: {str(e)}",
        ) from e
    except Exception as e:
        logger.error("Error saving GitHub App credentials", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error while saving credentials",
        ) from e


@github_router.get("/credentials/status", response_model=GitHubAppCredentialsStatus)
async def get_github_app_credentials_status(
    *,
    session: AsyncDBSession,
    role: Annotated[
        Role, RoleACL(allow_user=True, allow_service=False, require_workspace="no")
    ],
) -> GitHubAppCredentialsStatus:
    """Get the status of GitHub App credentials."""
    # Organization-level operation, no specific checks needed since this is org VCS

    try:
        github_service = GitHubAppService(session=session, role=role)
        status_data = await github_service.get_github_app_credentials_status()
        return GitHubAppCredentialsStatus(**status_data)

    except Exception as e:
        logger.error("Error getting GitHub App credentials status", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error while getting credentials status",
        ) from e


# Mount GitHub sub-router to organization VCS router after all endpoints are defined
org_router.include_router(github_router)
