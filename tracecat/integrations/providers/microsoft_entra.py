"""Microsoft Entra ID OAuth integration for identity and access management."""

from typing import ClassVar

from tracecat.integrations.models import (
    ProviderCategory,
    ProviderMetadata,
    ProviderScopes,
)
from tracecat.integrations.providers.microsoft import MicrosoftACProvider

# Microsoft Entra ID specific scopes
MICROSOFT_ENTRA_SCOPES = ProviderScopes(
    default=[
        "offline_access",
        "https://graph.microsoft.com/User.Read",
        "https://graph.microsoft.com/Directory.Read.All",
        "https://graph.microsoft.com/Group.Read.All",
    ],
    allowed_patterns=[
        r"^https://graph\.microsoft\.com/User\.[^/]+$",
        r"^https://graph\.microsoft\.com/Directory\.[^/]+$",
        r"^https://graph\.microsoft\.com/Group\.[^/]+$",
        r"^https://graph\.microsoft\.com/Application\.[^/]+$",
        r"^https://graph\.microsoft\.com/RoleManagement\.[^/]+$",
        r"^https://graph\.microsoft\.com/Policy\.[^/]+$",
        r"^https://graph\.microsoft\.com/Organization\.[^/]+$",
        # Security restrictions - prevent dangerous all-access scopes
        r"^(?!.*\.ReadWrite\.All$).*",
        r"^(?!.*\.Write\.All$).*",
        r"^(?!.*\.FullControl\.All$).*",
    ],
)

# Microsoft Entra ID specific metadata
MICROSOFT_ENTRA_METADATA = ProviderMetadata(
    id="microsoft_entra",
    name="Microsoft Entra ID",
    description="Microsoft Entra ID OAuth provider for identity and access management",
    categories=[ProviderCategory.AUTH],
    setup_steps=[
        "Register your application in Azure Portal",
        "Add the redirect URI shown above to 'Redirect URIs'",
        "Configure required API permissions for Microsoft Graph (User, Directory, Group)",
        "Grant admin consent for directory permissions if required",
        "Copy Client ID and Client Secret",
        "Configure credentials in Tracecat with your tenant ID",
    ],
    enabled=False,
    api_docs_url="https://learn.microsoft.com/en-us/graph/api/resources/azure-ad-overview?view=graph-rest-1.0",
    setup_guide_url="https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app",
    troubleshooting_url="https://learn.microsoft.com/en-us/entra/identity-platform/reference-error-codes",
)


class MicrosoftEntraOAuthProvider(MicrosoftACProvider):
    """Microsoft Entra ID OAuth provider for identity and access management."""

    id: ClassVar[str] = "microsoft_entra"

    # Use Entra-specific constants
    scopes: ClassVar[ProviderScopes] = MICROSOFT_ENTRA_SCOPES
    metadata: ClassVar[ProviderMetadata] = MICROSOFT_ENTRA_METADATA
