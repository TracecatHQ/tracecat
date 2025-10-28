"""Microsoft Defender for Endpoint OAuth integration."""

from __future__ import annotations

from typing import Any, ClassVar, Unpack

from pydantic import BaseModel, Field, field_validator

from tracecat.integrations.models import (
    OAuthProviderKwargs,
    ProviderMetadata,
    ProviderScopes,
)
from tracecat.integrations.providers.base import (
    AuthorizationCodeOAuthProvider,
    ClientCredentialsOAuthProvider,
)
from tracecat.integrations.providers.microsoft.clouds import (
    AzureCloud,
    get_authorization_endpoint,
    get_defender_scopes,
    get_token_endpoint,
)

SETUP_STEPS = [
    "Register a Microsoft Entra application with access to Microsoft Defender for Endpoint",
    "Add the redirect URI shown above to Redirect URIs",
    "Configure the required Defender for Endpoint delegated or application permissions and grant admin consent",
    "Copy the client ID, client secret, and tenant ID",
    "In Tracecat, configure your credentials with the tenant ID, selected cloud, and optional API resource override for sovereign clouds",
]


class DefenderEndpointOAuthConfig(BaseModel):
    """Configuration model for Microsoft Defender for Endpoint OAuth provider."""

    tenant_id: str = Field(
        ...,
        description="Microsoft Entra tenant ID. Use 'common' for multi-tenant apps, 'organizations' for work/school accounts, or a specific tenant GUID.",
        min_length=1,
        max_length=100,
    )
    cloud: AzureCloud = Field(
        default=AzureCloud.PUBLIC,
        description="Microsoft cloud environment. Use 'public' or 'us_gov' (GCC High/DoD).",
    )
    resource_override: str | None = Field(
        default=None,
        description="Optional Defender for Endpoint resource URI override (e.g. https://api-gcc.securitycenter.microsoft.us for GCC tenants).",
        pattern=r"^https://",
    )

    @field_validator("resource_override")
    @classmethod
    def _strip_trailing_slash(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.rstrip("/")


AC_SCOPES = ProviderScopes(
    default=["offline_access", "https://api.securitycenter.microsoft.com/.default"],
)

AC_METADATA = ProviderMetadata(
    id="microsoft_defender_endpoint",
    name="Microsoft Defender for Endpoint (Delegated)",
    description="Microsoft Defender for Endpoint delegated authentication for investigation and response APIs.",
    setup_steps=SETUP_STEPS,
    enabled=True,
    api_docs_url="https://learn.microsoft.com/en-us/defender-endpoint/api/",
    setup_guide_url="https://learn.microsoft.com/en-us/defender-endpoint/api/get-started",
    troubleshooting_url="https://learn.microsoft.com/en-us/defender-endpoint/api/common-errors",
)


class MicrosoftDefenderEndpointACProvider(AuthorizationCodeOAuthProvider):
    """Microsoft Defender for Endpoint OAuth provider for delegated user permissions."""

    id: ClassVar[str] = "microsoft_defender_endpoint"
    scopes: ClassVar[ProviderScopes] = AC_SCOPES
    config_model: ClassVar[type[BaseModel]] = DefenderEndpointOAuthConfig
    metadata: ClassVar[ProviderMetadata] = AC_METADATA

    def __init__(
        self,
        tenant_id: str,
        cloud: AzureCloud = AzureCloud.PUBLIC,
        resource_override: str | None = None,
        **kwargs: Unpack[OAuthProviderKwargs],
    ):
        """Initialize the Microsoft Defender for Endpoint OAuth provider."""
        self.tenant_id = tenant_id
        self.cloud = AzureCloud(cloud)
        self.resource_override = (
            resource_override.rstrip("/") if resource_override else None
        )
        if kwargs.get("scopes") is None:
            kwargs["scopes"] = get_defender_scopes(
                self.cloud, delegated=True, resource_override=self.resource_override
            )
        super().__init__(**kwargs)

    @property
    def authorization_endpoint(self) -> str:
        return get_authorization_endpoint(self.cloud, self.tenant_id)

    @property
    def token_endpoint(self) -> str:
        return get_token_endpoint(self.cloud, self.tenant_id)

    def _get_additional_authorize_params(self) -> dict[str, Any]:
        """Add Microsoft-specific authorization parameters."""
        return {
            "response_mode": "query",
            "prompt": "select_account",
        }


CC_SCOPES = ProviderScopes(
    default=["https://api.securitycenter.microsoft.com/.default"],
)

CC_METADATA = ProviderMetadata(
    id="microsoft_defender_endpoint",
    name="Microsoft Defender for Endpoint (Service Principal)",
    description="Microsoft Defender for Endpoint service principal authentication for automated investigation and response.",
    setup_steps=SETUP_STEPS,
    enabled=True,
    api_docs_url="https://learn.microsoft.com/en-us/defender-endpoint/api/",
    setup_guide_url="https://learn.microsoft.com/en-us/defender-endpoint/api/get-started",
    troubleshooting_url="https://learn.microsoft.com/en-us/defender-endpoint/api/common-errors",
)


class MicrosoftDefenderEndpointCCProvider(ClientCredentialsOAuthProvider):
    """Microsoft Defender for Endpoint OAuth provider using client credentials flow."""

    id: ClassVar[str] = "microsoft_defender_endpoint"
    scopes: ClassVar[ProviderScopes] = CC_SCOPES
    config_model: ClassVar[type[BaseModel]] = DefenderEndpointOAuthConfig
    metadata: ClassVar[ProviderMetadata] = CC_METADATA

    def __init__(
        self,
        tenant_id: str,
        cloud: AzureCloud = AzureCloud.PUBLIC,
        resource_override: str | None = None,
        **kwargs: Unpack[OAuthProviderKwargs],
    ):
        """Initialize the Microsoft Defender for Endpoint client credentials provider."""
        self.tenant_id = tenant_id
        self.cloud = AzureCloud(cloud)
        self.resource_override = (
            resource_override.rstrip("/") if resource_override else None
        )
        if kwargs.get("scopes") is None:
            kwargs["scopes"] = get_defender_scopes(
                self.cloud, delegated=False, resource_override=self.resource_override
            )
        super().__init__(**kwargs)

    @property
    def authorization_endpoint(self) -> str:
        return get_authorization_endpoint(self.cloud, self.tenant_id)

    @property
    def token_endpoint(self) -> str:
        return get_token_endpoint(self.cloud, self.tenant_id)
