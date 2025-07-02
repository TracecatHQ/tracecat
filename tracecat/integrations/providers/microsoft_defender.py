"""Microsoft Defender OAuth integration for security and threat protection."""

from typing import ClassVar

from tracecat.integrations.models import (
    ProviderCategory,
    ProviderMetadata,
    ProviderScopes,
)
from tracecat.integrations.providers.microsoft import MicrosoftOAuthProvider


class MicrosoftDefenderOAuthProvider(MicrosoftOAuthProvider):
    """Microsoft Defender OAuth provider for security and threat protection."""

    id: ClassVar[str] = "microsoft_defender"

    # Defender specific scopes for security and threat protection
    scopes: ClassVar[ProviderScopes] = ProviderScopes(
        default=[
            "offline_access",
            "https://graph.microsoft.com/User.Read",
            "https://graph.microsoft.com/SecurityEvents.Read.All",
            "https://graph.microsoft.com/ThreatIndicators.Read.All",
            "https://graph.microsoft.com/SecurityActions.Read.All",
        ],
        allowed_patterns=[
            r"^https://graph\.microsoft\.com/User\.[^/]+$",
            r"^https://graph\.microsoft\.com/SecurityEvents\.[^/]+$",
            r"^https://graph\.microsoft\.com/ThreatIndicators\.[^/]+$",
            r"^https://graph\.microsoft\.com/SecurityActions\.[^/]+$",
            r"^https://graph\.microsoft\.com/Security\.[^/]+$",
            r"^https://graph\.microsoft\.com/ThreatAssessment\.[^/]+$",
            r"^https://graph\.microsoft\.com/InformationProtection\.[^/]+$",
            r"^https://api\.security\.microsoft\.com/.*$",
            r"^https://api\.securitycenter\.microsoft\.com/.*$",
            # Security restrictions - prevent dangerous all-access scopes
            r"^(?!.*\.ReadWrite\.All$).*",
            r"^(?!.*\.Write\.All$).*",
            r"^(?!.*\.FullControl\.All$).*",
        ],
    )

    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="microsoft_defender",
        name="Microsoft Defender",
        description="Microsoft Defender OAuth provider for security and threat protection",
        categories=[ProviderCategory.MONITORING, ProviderCategory.ALERTING],
        setup_steps=[
            "Register your application in Azure Portal",
            "Add the redirect URI shown above to 'Redirect URIs'",
            "Configure required API permissions for Microsoft Graph Security APIs",
            "Grant admin consent for security-related permissions",
            "Enable Microsoft Defender APIs in your tenant if required",
            "Copy Client ID and Client Secret",
            "Configure credentials in Tracecat with your tenant ID",
        ],
        enabled=True,
        api_docs_url="https://learn.microsoft.com/en-us/graph/api/resources/security-api-overview?view=graph-rest-1.0",
        setup_guide_url="https://learn.microsoft.com/en-us/microsoft-365/security/defender/api-create-app-web?view=o365-worldwide",
        troubleshooting_url="https://learn.microsoft.com/en-us/microsoft-365/security/defender/troubleshoot-api?view=o365-worldwide",
    )
