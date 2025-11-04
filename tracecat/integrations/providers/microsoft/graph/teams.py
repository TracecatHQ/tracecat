"""Microsoft Teams OAuth integration using Microsoft Graph provider."""

from typing import ClassVar

from tracecat.integrations.providers.microsoft.graph.provider import (
    MicrosoftGraphACProvider,
    MicrosoftGraphCCProvider,
    get_graph_ac_metadata,
    get_graph_cc_metadata,
)
from tracecat.integrations.schemas import ProviderMetadata, ProviderScopes


class MicrosoftTeamsACProvider(MicrosoftGraphACProvider):
    """Microsoft Teams OAuth provider using authorization code flow for delegated user permissions."""

    id: ClassVar[str] = "microsoft_teams"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(
        default=[
            "offline_access",
            "https://graph.microsoft.com/User.Read",
            "https://graph.microsoft.com/ChatMessage.Read",
            "https://graph.microsoft.com/ChatMessage.Send",
        ],
    )
    metadata: ClassVar[ProviderMetadata] = get_graph_ac_metadata(
        id="microsoft_teams",
        name="Microsoft Teams",
    )


class MicrosoftTeamsCCProvider(MicrosoftGraphCCProvider):
    """Microsoft Teams OAuth provider using client credentials flow for application permissions (service account)."""

    id: ClassVar[str] = "microsoft_teams"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(
        default=[
            "https://graph.microsoft.com/.default",
        ],
    )
    metadata: ClassVar[ProviderMetadata] = get_graph_cc_metadata(
        id="microsoft_teams",
        name="Microsoft Teams",
    )
