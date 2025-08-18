import asyncio

from fastapi import HTTPException, status
from fastapi.responses import RedirectResponse
from github import Github
from github.GithubException import GithubException
from pydantic import SecretStr

from tracecat.db.dependencies import AsyncDBSession
from tracecat.logger import logger
from tracecat.types.auth import Role
from tracecat.vcs.github.app import GitHubAppService


async def handle_manifest_conversion(
    session: AsyncDBSession,
    role: Role,
    code: str,
    state: str | None = None,
) -> RedirectResponse:
    """Handle GitHub App manifest conversion from temporary code.

    This is used when we create a new GitHub App from a manifest for the organization.
    """
    if not code.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Code parameter cannot be empty",
        )

    # TODO: Validate state parameter for CSRF protection if provided
    if state:
        logger.debug(
            "State parameter received",
            state=state[:8] + "..." if len(state) > 8 else state,
        )

    logger.info("Converting GitHub App manifest", code=code[:8] + "...")

    try:
        # Use PyGithub's requester to call the conversion endpoint
        # Note: No Authorization header needed for this endpoint
        github = Github()
        _, data = await asyncio.to_thread(
            github.requester.requestJsonAndCheck,
            "POST",
            f"/app-manifests/{code}/conversions",
        )

        # Extract app credentials from response
        app_id = str(data["id"])
        private_key_pem = data["pem"]
        webhook_secret = data.get("webhook_secret")
        client_id = data.get("client_id")
        slug = data.get("slug")

        # Store the app credentials using GitHub service
        github_service = GitHubAppService(session=session, role=role)
        await github_service.register_app(
            app_id=app_id,
            private_key_pem=SecretStr(private_key_pem),
            webhook_secret=SecretStr(webhook_secret) if webhook_secret else None,
            client_id=client_id,
        )

        logger.info(
            "Successfully converted GitHub App manifest",
            app_id=app_id,
            slug=slug,
        )

        # Redirect to GitHub installation page
        if slug:
            # Include organization in the installation URL for proper scoping
            org_name = data.get("owner", {}).get("login")
            if org_name:
                redirect_url = f"https://github.com/organizations/{org_name}/settings/installations"
            else:
                redirect_url = f"https://github.com/apps/{slug}/installations/new"
        else:
            redirect_url = "https://github.com/settings/apps"
            logger.warning("No slug returned, redirecting to apps settings")

        return RedirectResponse(status_code=status.HTTP_302_FOUND, url=redirect_url)

    except GithubException as e:
        logger.error(
            "GitHub API error during manifest conversion",
            status_code=e.status,
            data=e.data,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"GitHub App manifest conversion failed: {(e.data or {}).get('message', 'Unknown error')}",
        ) from e
    except Exception as e:
        logger.error("Error converting GitHub App manifest", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to convert GitHub App manifest",
        ) from e
