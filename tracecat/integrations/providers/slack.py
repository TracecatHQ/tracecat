"""Slack OAuth integration using generic OAuth provider."""

from typing import ClassVar

from tracecat.integrations.base import BaseOauthProvider


class SlackOAuthProvider(BaseOauthProvider):
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
