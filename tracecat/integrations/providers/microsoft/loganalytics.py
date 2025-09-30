"""Azure Log Analytics OAuth integration for KQL query execution."""

from typing import Any, ClassVar, Unpack

from pydantic import BaseModel, Field

from tracecat.integrations.models import (
    OAuthProviderKwargs,
    ProviderMetadata,
    ProviderScopes,
)
from tracecat.integrations.providers.base import (
    AuthorizationCodeOAuthProvider,
)

AUTHORIZATION_ENDPOINT = (
    "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize"
)
TOKEN_ENDPOINT = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"


def get_loganalytics_setup_steps() -> list[str]:
    """Get setup steps for Log Analytics OAuth."""
    return [
        "Register your application in Azure Portal",
        "Add the redirect URI shown above to 'Redirect URIs'",
        "Configure Azure Log Analytics API permissions",
        "Add delegated permission: Data.Read (https://api.loganalytics.io/Data.Read)",
        "Copy Client ID and Client Secret",
        "Configure credentials in Tracecat with your tenant ID",
    ]


AC_DESCRIPTION = "OAuth provider for Azure Log Analytics KQL query execution"
AC_DEFAULT_SCOPES = [
    "offline_access",
    "https://api.loganalytics.io/.default",
]


class AzureLogAnalyticsOAuthConfig(BaseModel):
    """Configuration model for Azure Log Analytics OAuth provider."""

    tenant_id: str = Field(
        ...,
        description="Azure AD tenant ID. 'common' for multi-tenant apps, 'organizations' for work/school accounts, or a specific tenant GUID.",
        min_length=1,
        max_length=100,
    )


AC_SCOPES = ProviderScopes(
    default=AC_DEFAULT_SCOPES,
)

AC_METADATA = ProviderMetadata(
    id="azure_log_analytics",
    name="Azure Log Analytics (Delegated)",
    description=f"Azure Log Analytics {AC_DESCRIPTION}",
    setup_steps=get_loganalytics_setup_steps(),
    enabled=True,
    api_docs_url="https://learn.microsoft.com/en-us/rest/api/loganalytics/",
    setup_guide_url="https://learn.microsoft.com/en-us/azure/azure-monitor/logs/api/overview",
    troubleshooting_url="https://learn.microsoft.com/en-us/azure/azure-monitor/logs/api/errors",
)


class AzureLogAnalyticsACProvider(AuthorizationCodeOAuthProvider):
    """Azure Log Analytics OAuth provider for KQL query execution with delegated permissions."""

    id: ClassVar[str] = "azure_log_analytics"
    _authorization_endpoint: ClassVar[str] = AUTHORIZATION_ENDPOINT
    _token_endpoint: ClassVar[str] = TOKEN_ENDPOINT
    scopes: ClassVar[ProviderScopes] = AC_SCOPES
    config_model: ClassVar[type[BaseModel]] = AzureLogAnalyticsOAuthConfig
    metadata: ClassVar[ProviderMetadata] = AC_METADATA

    def __init__(
        self,
        tenant_id: str,
        **kwargs: Unpack[OAuthProviderKwargs],
    ):
        """Initialize the Azure Log Analytics OAuth provider."""
        self.tenant_id = tenant_id
        super().__init__(**kwargs)

    @property
    def authorization_endpoint(self) -> str:
        return self._authorization_endpoint.format(tenant=self.tenant_id)

    @property
    def token_endpoint(self) -> str:
        return self._token_endpoint.format(tenant=self.tenant_id)

    def _get_additional_authorize_params(self) -> dict[str, Any]:
        """Add Azure-specific authorization parameters."""
        return {
            "response_mode": "query",
            "prompt": "select_account",
        }
