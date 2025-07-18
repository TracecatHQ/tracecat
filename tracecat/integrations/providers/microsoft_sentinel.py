"""Microsoft Sentinel OAuth integration for SIEM and security orchestration."""

from typing import ClassVar

from tracecat.integrations.models import (
    ProviderCategory,
    ProviderMetadata,
    ProviderScopes,
)
from tracecat.integrations.providers.microsoft import MicrosoftACProvider


class MicrosoftSentinelOAuthProvider(MicrosoftACProvider):
    """Microsoft Sentinel OAuth provider for SIEM and security orchestration."""

    id: ClassVar[str] = "microsoft_sentinel"

    # Sentinel specific scopes for SIEM and security orchestration
    scopes: ClassVar[ProviderScopes] = ProviderScopes(
        default=[
            "offline_access",
            "https://graph.microsoft.com/User.Read",
            "https://graph.microsoft.com/SecurityEvents.Read.All",
            "https://graph.microsoft.com/SecurityIncident.Read.All",
            "https://management.azure.com/user_impersonation",
        ],
        allowed_patterns=[
            r"^https://graph\.microsoft\.com/User\.[^/]+$",
            r"^https://graph\.microsoft\.com/SecurityEvents\.[^/]+$",
            r"^https://graph\.microsoft\.com/SecurityIncident\.[^/]+$",
            r"^https://graph\.microsoft\.com/Security\.[^/]+$",
            r"^https://graph\.microsoft\.com/ThreatIntelligence\.[^/]+$",
            r"^https://management\.azure\.com/.*$",
            r"^https://api\.loganalytics\.io/.*$",
            r"^https://.*\.ods\.opinsights\.azure\.com/.*$",
            # Security restrictions - prevent dangerous all-access scopes
            r"^(?!.*\.ReadWrite\.All$).*",
            r"^(?!.*\.Write\.All$).*",
            r"^(?!.*\.FullControl\.All$).*",
        ],
    )

    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="microsoft_sentinel",
        name="Microsoft Sentinel",
        description="Microsoft Sentinel OAuth provider for SIEM and security orchestration",
        categories=[ProviderCategory.MONITORING, ProviderCategory.ALERTING],
        setup_steps=[
            "Register your application in Azure Portal",
            "Add the redirect URI shown above to 'Redirect URIs'",
            "Configure required API permissions for Microsoft Graph Security and Azure Management",
            "Grant admin consent for security and management permissions",
            "Ensure your application has access to the Log Analytics workspace",
            "Configure Azure RBAC roles for Sentinel workspace access",
            "Copy Client ID and Client Secret",
            "Configure credentials in Tracecat with your tenant ID",
        ],
        enabled=False,
        api_docs_url="https://learn.microsoft.com/en-us/rest/api/securityinsights/",
        setup_guide_url="https://learn.microsoft.com/en-us/azure/sentinel/connect-rest-api-template",
        troubleshooting_url="https://learn.microsoft.com/en-us/azure/sentinel/troubleshooting",
    )
