"""Microsoft Teams OAuth integration for collaboration and communication."""

from typing import ClassVar

from tracecat.integrations.models import (
    ProviderCategory,
    ProviderMetadata,
    ProviderScopes,
)
from tracecat.integrations.providers.microsoft import (
    MicrosoftACProvider,
    MicrosoftCCProvider,
)

# Microsoft Teams specific scopes for authorization code flow
AC_SCOPES = ProviderScopes(
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

# Microsoft Teams specific metadata for authorization code flow
AC_METADATA = ProviderMetadata(
    id="microsoft_teams_ac",
    name="Microsoft Teams",
    description="Microsoft Teams OAuth provider (Delegated user)",
    categories=[ProviderCategory.COMMUNICATION],
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


class MicrosoftTeamsACProvider(MicrosoftACProvider):
    """Microsoft Teams OAuth provider for collaboration and communication."""

    id: ClassVar[str] = "microsoft_teams_ac"

    # Use Teams-specific constants
    scopes: ClassVar[ProviderScopes] = AC_SCOPES
    metadata: ClassVar[ProviderMetadata] = AC_METADATA


# Microsoft Teams specific scopes for client credentials flow
CC_SCOPES = ProviderScopes(
    default=[
        "https://graph.microsoft.com/.default",
    ],
    accepts_additional_scopes=False,
)

# Microsoft Teams specific metadata for client credentials flow
CC_METADATA = ProviderMetadata(
    id="microsoft_teams_cc",
    name="Microsoft Teams",
    description="Microsoft Teams OAuth provider (Service account)",
    categories=[ProviderCategory.COMMUNICATION],
    setup_steps=[
        "Register your application in Azure Portal",
        "Configure API permissions for Microsoft Graph with Application permissions (not Delegated)",
        "Enable Teams-specific application permissions (Chat.Read.All, Team.ReadBasic.All, etc.)",
        "Grant admin consent for the application permissions",
        "Copy Client ID and Client Secret",
        "Configure credentials in Tracecat with your tenant ID",
        "Use scopes like 'https://graph.microsoft.com/.default' for client credentials flow",
    ],
    enabled=True,
    api_docs_url="https://learn.microsoft.com/en-us/graph/api/resources/teams-api-overview?view=graph-rest-1.0",
    setup_guide_url="https://learn.microsoft.com/en-us/graph/auth-v2-service",
    troubleshooting_url="https://learn.microsoft.com/en-us/microsoftteams/platform/troubleshoot/troubleshooting",
)


class MicrosoftTeamsCCProvider(MicrosoftCCProvider):
    """Microsoft Teams OAuth provider using client credentials flow for server-to-server automation."""

    id: ClassVar[str] = "microsoft_teams_cc"

    # Use Teams-specific client credentials constants
    scopes: ClassVar[ProviderScopes] = CC_SCOPES
    metadata: ClassVar[ProviderMetadata] = CC_METADATA
