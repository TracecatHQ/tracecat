"""Slack OAuth integration using generic OAuth provider."""

from typing import ClassVar

from tracecat.integrations.base import BaseOAuthProvider
from tracecat.integrations.models import ProviderCategory, ProviderMetadata


class SlackOAuthProvider(BaseOAuthProvider):
    """Slack OAuth provider using generic OAuth implementation."""

    id: ClassVar[str] = "slack"

    # Slack OAuth endpoints
    authorization_endpoint: ClassVar[str] = "https://slack.com/oauth/v2/authorize"
    token_endpoint: ClassVar[str] = "https://slack.com/api/oauth.v2.access"

    # Default Slack scopes
    default_scopes: ClassVar[list[str]] = [
        "channels:read",
        "chat:write",
        "users:read",
        "team:read",
        "im:history",
        "channels:history",
    ]

    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="slack",
        name="Slack",
        description="Slack OAuth provider for team communication and notifications",
        categories=[ProviderCategory.COMMUNICATION],
        features=[
            "Channel Notifications",
            "Direct Messages",
            "Custom Webhooks",
            "Bot Integration",
        ],
        setup_steps=[
            "Create a Slack App in your workspace",
            "Configure OAuth & Permissions",
            "Add required bot token scopes",
            "Install the app to your workspace",
            "Add client ID and secret",
            "Test the connection",
        ],
    )
