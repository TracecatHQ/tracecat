"""Slack OAuth integration using generic OAuth provider."""

from typing import ClassVar

from tracecat.integrations.base import AuthorizationCodeOAuthProvider
from tracecat.integrations.models import (
    ProviderCategory,
    ProviderMetadata,
    ProviderScopes,
)


class SlackOAuthProvider(AuthorizationCodeOAuthProvider):
    """Slack OAuth provider using generic OAuth implementation."""

    id: ClassVar[str] = "slack"

    # Slack OAuth endpoints
    _authorization_endpoint: ClassVar[str] = "https://slack.com/oauth/v2/authorize"
    _token_endpoint: ClassVar[str] = "https://slack.com/api/oauth.v2.access"

    # Slack OAuth scopes
    scopes: ClassVar[ProviderScopes] = ProviderScopes(
        default=[
            "channels:read",
            "chat:write",
            "users:read",
            "team:read",
            "im:history",
            "channels:history",
        ]
    )

    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="slack",
        name="Slack",
        description="Slack OAuth provider for team communication and notifications",
        categories=[ProviderCategory.COMMUNICATION],
        setup_steps=[
            "Create a new Slack App at api.slack.com/apps",
            "Go to OAuth & Permissions section",
            "Add the redirect URI shown above to 'Redirect URLs'",
            "Add required bot token scopes under 'Scopes'",
            "Install the app to your workspace",
            "Copy Client ID and Client Secret from Basic Information",
            "Configure credentials in Tracecat",
        ],
        enabled=False,
    )
