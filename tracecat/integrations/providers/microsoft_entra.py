"""Microsoft Entra ID OAuth integration for identity and access management."""

from typing import ClassVar

from tracecat.integrations.models import (
    ProviderCategory,
    ProviderMetadata,
    ProviderScopes,
)
from tracecat.integrations.providers.microsoft import MicrosoftOAuthProvider


class MicrosoftEntraOAuthProvider(MicrosoftOAuthProvider):
    """Microsoft Entra ID OAuth provider for identity and access management."""

    id: ClassVar[str] = "microsoft_entra"

    # Entra ID specific scopes for identity management
    scopes: ClassVar[ProviderScopes] = ProviderScopes(
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

    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="microsoft_entra",
        name="Microsoft Entra ID",
        description="Microsoft Entra ID OAuth provider for identity and access management",
        categories=[ProviderCategory.AUTH],
        features=[
            "OAuth 2.0",
            "Azure AD Integration",
            "Identity Management",
            "Single Sign-On",
            "User Management",
            "Group Management",
            "Directory Services",
            "Role-Based Access Control",
        ],
        setup_steps=[
            "Register your application in Azure Portal",
            "Add the redirect URI shown above to 'Redirect URIs'",
            "Configure required API permissions for Microsoft Graph (User, Directory, Group)",
            "Grant admin consent for directory permissions if required",
            "Copy Client ID and Client Secret",
            "Configure credentials in Tracecat with your tenant ID",
        ],
        enabled=True,
        api_docs_url="https://learn.microsoft.com/en-us/graph/api/resources/azure-ad-overview?view=graph-rest-1.0",
        setup_guide_url="https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app",
        troubleshooting_url="https://learn.microsoft.com/en-us/entra/identity-platform/reference-error-codes",
    )
