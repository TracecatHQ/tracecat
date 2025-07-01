"""Microsoft Teams OAuth integration for collaboration and communication."""

from typing import ClassVar

from tracecat.integrations.models import (
    ProviderCategory,
    ProviderMetadata,
    ProviderScopes,
)
from tracecat.integrations.providers.microsoft import MicrosoftOAuthProvider


class MicrosoftTeamsOAuthProvider(MicrosoftOAuthProvider):
    """Microsoft Teams OAuth provider for collaboration and communication."""

    id: ClassVar[str] = "microsoft_teams"

    # Teams specific scopes for collaboration and communication
    scopes: ClassVar[ProviderScopes] = ProviderScopes(
        default=[
            "offline_access",
            "https://graph.microsoft.com/User.Read",
            "https://graph.microsoft.com/Chat.Read",
            "https://graph.microsoft.com/Team.ReadBasic.All",
            "https://graph.microsoft.com/Channel.ReadBasic.All",
        ],
        allowed_patterns=[
            r"^https://graph\.microsoft\.com/User\.[^/]+$",
            r"^https://graph\.microsoft\.com/Chat\.[^/]+$",
            r"^https://graph\.microsoft\.com/Team\.[^/]+$",
            r"^https://graph\.microsoft\.com/Channel\.[^/]+$",
            r"^https://graph\.microsoft\.com/TeamMember\.[^/]+$",
            r"^https://graph\.microsoft\.com/TeamsTab\.[^/]+$",
            r"^https://graph\.microsoft\.com/OnlineMeeting\.[^/]+$",
            r"^https://graph\.microsoft\.com/Presence\.[^/]+$",
            r"^https://graph\.microsoft\.com/Files\.[^/]+$",
            r"^https://graph\.microsoft\.com/Sites\.[^/]+$",
            # Security restrictions - prevent dangerous all-access scopes
            r"^(?!.*\.ReadWrite\.All$).*",
            r"^(?!.*\.Write\.All$).*",
            r"^(?!.*\.FullControl\.All$).*",
        ],
    )

    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="microsoft_teams",
        name="Microsoft Teams",
        description="Microsoft Teams OAuth provider for collaboration and communication",
        categories=[ProviderCategory.COMMUNICATION],
        features=[
            "OAuth 2.0",
            "Teams Integration",
            "Chat Management",
            "Channel Management",
            "Meeting Integration",
            "File Sharing",
            "Team Collaboration",
            "Presence Status",
            "Message Automation",
        ],
        setup_steps=[
            "Register your application in Azure Portal",
            "Add the redirect URI shown above to 'Redirect URIs'",
            "Configure required API permissions for Microsoft Graph (Chat, Team, Channel)",
            "Enable Teams-specific permissions in app manifest if building a Teams app",
            "Copy Client ID and Client Secret",
            "Configure credentials in Tracecat with your tenant ID",
        ],
        enabled=True,
        api_docs_url="https://learn.microsoft.com/en-us/graph/api/resources/teams-api-overview?view=graph-rest-1.0",
        setup_guide_url="https://learn.microsoft.com/en-us/microsoftteams/platform/graph-api/rsc/resource-specific-consent",
        troubleshooting_url="https://learn.microsoft.com/en-us/microsoftteams/platform/troubleshoot/troubleshooting",
    )
