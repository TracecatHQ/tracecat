"""Azure Management OAuth integration for Azure Resource Manager APIs."""

from typing import ClassVar

from tracecat.integrations.providers.microsoft.common import (
    MicrosoftAuthorizationCodeOAuthProvider,
    MicrosoftClientCredentialsOAuthProvider,
    get_ac_description,
    get_cc_description,
)
from tracecat.integrations.schemas import ProviderMetadata, ProviderScopes

AZURE_MANAGEMENT_API_DOCS_URL = "https://learn.microsoft.com/en-us/rest/api/azure/"
AZURE_MANAGEMENT_SETUP_GUIDE_URL = "https://learn.microsoft.com/en-us/azure/active-directory/develop/quickstart-register-app"
AZURE_MANAGEMENT_TROUBLESHOOTING_URL = "https://learn.microsoft.com/en-us/azure/active-directory/develop/reference-aadsts-error-codes"


def get_azure_ac_metadata(
    id: str,
    name: str,
    api_docs_url: str = AZURE_MANAGEMENT_API_DOCS_URL,
    setup_guide_url: str = AZURE_MANAGEMENT_SETUP_GUIDE_URL,
    troubleshooting_url: str = AZURE_MANAGEMENT_TROUBLESHOOTING_URL,
) -> ProviderMetadata:
    return ProviderMetadata(
        id=id,
        name=f"{name} (Delegated)",
        description=get_ac_description(name),
        requires_config=True,
        enabled=True,
        api_docs_url=api_docs_url,
        setup_guide_url=setup_guide_url,
        troubleshooting_url=troubleshooting_url,
    )


def get_azure_cc_metadata(
    id: str,
    name: str,
    api_docs_url: str = AZURE_MANAGEMENT_API_DOCS_URL,
    setup_guide_url: str = AZURE_MANAGEMENT_SETUP_GUIDE_URL,
    troubleshooting_url: str = AZURE_MANAGEMENT_TROUBLESHOOTING_URL,
) -> ProviderMetadata:
    return ProviderMetadata(
        id=id,
        name=f"{name} (Service principal)",
        description=get_cc_description(name),
        requires_config=True,
        enabled=True,
        api_docs_url=api_docs_url,
        setup_guide_url=setup_guide_url,
        troubleshooting_url=troubleshooting_url,
    )


class AzureManagementACProvider(MicrosoftAuthorizationCodeOAuthProvider):
    """Azure Management OAuth provider using authorization code flow for delegated user permissions."""

    id: ClassVar[str] = "azure_management"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(
        default=[
            "offline_access",
            "https://management.azure.com/user_impersonation",
        ],
    )
    metadata: ClassVar[ProviderMetadata] = get_azure_ac_metadata(
        id="azure_management", name="Azure Management"
    )


class AzureManagementCCProvider(MicrosoftClientCredentialsOAuthProvider):
    """Azure Management OAuth provider using client credentials flow for application permissions."""

    id: ClassVar[str] = "azure_management"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(
        default=["https://management.azure.com/.default"],
    )
    metadata: ClassVar[ProviderMetadata] = get_azure_cc_metadata(
        id="azure_management", name="Azure Management"
    )
