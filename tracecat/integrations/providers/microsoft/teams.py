"""Microsoft Teams OAuth integration using Microsoft Graph provider."""

from typing import ClassVar

from tracecat.integrations.models import ProviderMetadata, ProviderScopes
from tracecat.integrations.providers.base import (
    AuthorizationCodeOAuthProvider,
    ClientCredentialsOAuthProvider,
)
from tracecat.integrations.providers.microsoft._common import (
    DEFAULT_AUTHORIZATION_ENDPOINT,
    DEFAULT_TOKEN_ENDPOINT,
    get_ac_setup_steps,
    get_cc_setup_steps,
)

AC_SCOPES = ProviderScopes(
    default=[
        "offline_access",
        "https://graph.microsoft.com/User.Read",
        "https://graph.microsoft.com/Chat.Read",
        "https://graph.microsoft.com/Chat.ReadWrite",
        "https://graph.microsoft.com/ChannelMessage.Send",
    ],
)

CC_SCOPES = ProviderScopes(
    default=[
        "https://graph.microsoft.com/.default",
    ],
)

AC_METADATA = ProviderMetadata(
    id="microsoft_teams",
    name="Microsoft Teams (Delegated)",
    description="Microsoft Teams delegated authentication for chat, team, and channel management",
    setup_steps=get_ac_setup_steps(service="Microsoft Teams (Chat, Team, Channel)"),
    requires_config=True,
    enabled=True,
    api_docs_url="https://learn.microsoft.com/en-us/graph/api/resources/teams-api-overview?view=graph-rest-1.0",
    setup_guide_url="https://learn.microsoft.com/en-us/graph/auth-v2-user",
    troubleshooting_url="https://learn.microsoft.com/en-us/microsoftteams/platform/troubleshoot/troubleshooting",
)

CC_METADATA = ProviderMetadata(
    id="microsoft_teams",
    name="Microsoft Teams (Service Principal)",
    description="Microsoft Teams service principal authentication for chat, team, and channel management",
    setup_steps=get_cc_setup_steps(
        service="Microsoft Teams (ChatMessage.Read.All, ChatMessage.Send)"
    ),
    requires_config=True,
    enabled=True,
    api_docs_url="https://learn.microsoft.com/en-us/graph/api/resources/teams-api-overview?view=graph-rest-1.0",
    setup_guide_url="https://learn.microsoft.com/en-us/graph/auth-v2-service",
    troubleshooting_url="https://learn.microsoft.com/en-us/microsoftteams/platform/troubleshoot/troubleshooting",
)


class MicrosoftTeamsACProvider(AuthorizationCodeOAuthProvider):
    """Microsoft Teams OAuth provider using authorization code flow for delegated user permissions."""

    id: ClassVar[str] = "microsoft_teams"
    scopes: ClassVar[ProviderScopes] = AC_SCOPES
    metadata: ClassVar[ProviderMetadata] = AC_METADATA
    default_authorization_endpoint: ClassVar[str] = DEFAULT_AUTHORIZATION_ENDPOINT
    default_token_endpoint: ClassVar[str] = DEFAULT_TOKEN_ENDPOINT


class MicrosoftTeamsCCProvider(ClientCredentialsOAuthProvider):
    """Microsoft Teams OAuth provider using client credentials flow for application permissions (service principal)."""

    id: ClassVar[str] = "microsoft_teams"
    scopes: ClassVar[ProviderScopes] = CC_SCOPES
    metadata: ClassVar[ProviderMetadata] = CC_METADATA
    default_authorization_endpoint: ClassVar[str] = DEFAULT_AUTHORIZATION_ENDPOINT
    default_token_endpoint: ClassVar[str] = DEFAULT_TOKEN_ENDPOINT
