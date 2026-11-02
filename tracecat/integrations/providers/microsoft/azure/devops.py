"""Azure DevOps OAuth integration using Microsoft Entra ID tokens."""

from typing import ClassVar

from tracecat.integrations.providers.microsoft.azure.provider import (
    AzureManagementACProvider,
    AzureManagementCCProvider,
    get_azure_ac_metadata,
    get_azure_cc_metadata,
)
from tracecat.integrations.schemas import ProviderMetadata, ProviderScopes

# Well-known Entra application ID of the Azure DevOps resource.
AZURE_DEVOPS_RESOURCE_ID = "499b84ac-1321-427f-aa17-267ca6975798"
AZURE_DEVOPS_API_DOCS_URL = "https://learn.microsoft.com/en-us/rest/api/azure/devops/?view=azure-devops-rest-7.2"
AZURE_DEVOPS_SETUP_GUIDE_URL = "https://learn.microsoft.com/en-us/azure/devops/integrate/get-started/authentication/entra"
AZURE_DEVOPS_TROUBLESHOOTING_URL = "https://learn.microsoft.com/en-us/azure/devops/integrate/get-started/authentication/service-principal-managed-identity"


class AzureDevOpsACProvider(AzureManagementACProvider):
    """Azure DevOps OAuth provider using authorization code flow for delegated user permissions."""

    id: ClassVar[str] = "azure_devops"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(
        default=[
            "offline_access",
            f"{AZURE_DEVOPS_RESOURCE_ID}/user_impersonation",
        ],
    )
    metadata: ClassVar[ProviderMetadata] = get_azure_ac_metadata(
        id="azure_devops",
        name="Azure DevOps",
        api_docs_url=AZURE_DEVOPS_API_DOCS_URL,
        setup_guide_url=AZURE_DEVOPS_SETUP_GUIDE_URL,
        troubleshooting_url=AZURE_DEVOPS_TROUBLESHOOTING_URL,
    )


class AzureDevOpsCCProvider(AzureManagementCCProvider):
    """Azure DevOps OAuth provider using a service principal (client credentials flow).

    The service principal must be added to the Azure DevOps organization
    and granted access before its token is accepted.
    """

    id: ClassVar[str] = "azure_devops"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(
        default=[f"{AZURE_DEVOPS_RESOURCE_ID}/.default"],
    )
    metadata: ClassVar[ProviderMetadata] = get_azure_cc_metadata(
        id="azure_devops",
        name="Azure DevOps",
        api_docs_url=AZURE_DEVOPS_API_DOCS_URL,
        setup_guide_url=AZURE_DEVOPS_SETUP_GUIDE_URL,
        troubleshooting_url=AZURE_DEVOPS_TROUBLESHOOTING_URL,
    )
