"""GitHub App manifest generator for enterprise installations."""

from __future__ import annotations

from typing import TypedDict

from pydantic import BaseModel

from tracecat import config


class GitHubWebhookAttributes(TypedDict):
    """Type definition for GitHub webhook attributes."""

    url: str
    active: bool


class GitHubAppPermissions(TypedDict):
    """Type definition for GitHub App default permissions."""

    contents: str
    metadata: str
    pull_requests: str


class GitHubAppManifest(BaseModel):
    """GitHub App manifest for creating enterprise apps."""

    name: str
    url: str
    hook_attributes: GitHubWebhookAttributes
    redirect_url: str
    callback_urls: list[str]
    setup_url: str
    description: str
    public: bool
    default_permissions: GitHubAppPermissions
    default_events: list[str]


def generate_github_app_manifest() -> GitHubAppManifest:
    """Generate GitHub App manifest for enterprise installation.

    Returns:
        GitHub App manifest as GitHubAppManifest object
    """
    # Use the configured public URLs from environment
    public_app_url = config.TRACECAT__PUBLIC_APP_URL.rstrip("/")
    public_api_url = config.TRACECAT__PUBLIC_API_URL.rstrip("/")

    # Check if we're using localhost (for development)
    is_localhost = "localhost" in public_api_url or "127.0.0.1" in public_api_url

    # For localhost development, use a placeholder webhook URL that GitHub can validate
    # but won't actually receive events (webhook will be configured later)
    if is_localhost:
        webhook_url = "https://example.com/webhook-placeholder"
        webhook_active = False
    else:
        webhook_url = f"{public_api_url}/organization/vcs/github/webhook"
        webhook_active = True

    return GitHubAppManifest(
        name="Tracecat Workflows",
        url=public_app_url,
        hook_attributes={
            "url": webhook_url,
            "active": webhook_active,
        },
        redirect_url=f"{public_app_url}/organization/vcs/github/install",
        # This needs to redirect to the UI instead of the API
        callback_urls=[f"{public_app_url}/organization/vcs/github/install"],
        setup_url=f"{public_app_url}/organization/vcs",
        description="GitHub App for Tracecat to manage workflow synchronization with Git repositories. This app enables automated pull request creation for workflow changes.",
        public=False,
        default_permissions={
            "contents": "write",
            "metadata": "read",
            "pull_requests": "write",
        },
        default_events=[
            "push",
            "pull_request",
        ],
    )
