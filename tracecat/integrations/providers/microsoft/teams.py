"""Microsoft Teams OAuth integration using Microsoft Graph provider."""

from typing import ClassVar

from tracecat.integrations.models import ProviderMetadata, ProviderScopes
from tracecat.integrations.providers.microsoft.graph import (
    AC_DESCRIPTION,
    CC_DESCRIPTION,
    MicrosoftGraphACProvider,
    MicrosoftGraphCCProvider,
    get_ac_setup_steps,
    get_cc_setup_steps,
)


def get_teams_ac_setup_steps() -> list[str]:
    """Get Teams-specific setup steps for authorization code flow."""
    return get_ac_setup_steps("Microsoft Teams (Chat, Team, Channel)")


def get_teams_cc_setup_steps() -> list[str]:
    """Get Teams-specific setup steps for client credentials flow."""
    return get_cc_setup_steps(
        "Microsoft Teams (ChatMessage.Read.All, ChatMessage.Send)"
    )


AC_SCOPES = ProviderScopes(
    default=[
        "offline_access",
        "https://graph.microsoft.com/User.Read",
        "https://graph.microsoft.com/ChatMessage.Read",
        "https://graph.microsoft.com/ChatMessage.Send",
    ],
)
AC_METADATA = ProviderMetadata(
    id="microsoft_teams",
    name="Microsoft Teams (Delegated)",
    description=f"Microsoft Teams {AC_DESCRIPTION}",
    setup_steps=get_teams_ac_setup_steps(),
    enabled=True,
    api_docs_url="https://learn.microsoft.com/en-us/graph/api/resources/teams-api-overview?view=graph-rest-1.0",
    setup_guide_url="https://learn.microsoft.com/en-us/microsoftteams/platform/graph-api/rsc/resource-specific-consent",
    troubleshooting_url="https://learn.microsoft.com/en-us/microsoftteams/platform/troubleshoot/troubleshooting",
)


class MicrosoftTeamsACProvider(MicrosoftGraphACProvider):
    """Microsoft Teams OAuth provider using authorization code flow for delegated user permissions."""

    id: ClassVar[str] = "microsoft_teams"
    scopes: ClassVar[ProviderScopes] = AC_SCOPES
    metadata: ClassVar[ProviderMetadata] = AC_METADATA


CC_SCOPES = ProviderScopes(
    default=[
        "https://graph.microsoft.com/.default",
    ],
)
CC_METADATA = ProviderMetadata(
    id="microsoft_teams",
    name="Microsoft Teams (Service account)",
    description=f"Microsoft Teams {CC_DESCRIPTION}",
    setup_steps=get_teams_cc_setup_steps(),
    enabled=True,
    api_docs_url="https://learn.microsoft.com/en-us/graph/api/resources/teams-api-overview?view=graph-rest-1.0",
    setup_guide_url="https://learn.microsoft.com/en-us/graph/auth-v2-service",
    troubleshooting_url="https://learn.microsoft.com/en-us/microsoftteams/platform/troubleshoot/troubleshooting",
)


class MicrosoftTeamsCCProvider(MicrosoftGraphCCProvider):
    """Microsoft Teams OAuth provider using client credentials flow for application permissions (service account)."""

    id: ClassVar[str] = "microsoft_teams"
    scopes: ClassVar[ProviderScopes] = CC_SCOPES
    metadata: ClassVar[ProviderMetadata] = CC_METADATA
