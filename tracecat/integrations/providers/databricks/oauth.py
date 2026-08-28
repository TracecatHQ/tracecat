"""Databricks OAuth providers for workspace APIs."""

from typing import ClassVar

from tracecat.integrations.providers.base import (
    AuthorizationCodeOAuthProvider,
    ClientCredentialsOAuthProvider,
)
from tracecat.integrations.schemas import ProviderMetadata, ProviderScopes

DATABRICKS_AUTHORIZATION_ENDPOINT = "https://{databricks-instance}/oidc/v1/authorize"
DATABRICKS_TOKEN_ENDPOINT = "https://{databricks-instance}/oidc/v1/token"
DATABRICKS_AUTH_DOCS_URL = "https://docs.databricks.com/aws/en/dev-tools/auth"
DATABRICKS_USER_SCOPES = ["all-apis", "offline_access"]
DATABRICKS_SERVICE_SCOPES = ["all-apis"]


class DatabricksACProvider(AuthorizationCodeOAuthProvider):
    """Databricks user-to-machine OAuth provider."""

    id: ClassVar[str] = "databricks"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(default=DATABRICKS_USER_SCOPES)
    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="databricks",
        name="Databricks (User OAuth)",
        description="Connect a Databricks user with OAuth to call workspace APIs.",
        requires_config=True,
        enabled=True,
        api_docs_url="https://docs.databricks.com/api/workspace/introduction",
        setup_guide_url=("https://docs.databricks.com/aws/en/dev-tools/auth/oauth-u2m"),
        troubleshooting_url=DATABRICKS_AUTH_DOCS_URL,
    )
    default_authorization_endpoint: ClassVar[str | None] = (
        DATABRICKS_AUTHORIZATION_ENDPOINT
    )
    default_token_endpoint: ClassVar[str | None] = DATABRICKS_TOKEN_ENDPOINT
    authorization_endpoint_help: ClassVar[str | list[str] | None] = [
        "Replace {databricks-instance} with your workspace hostname.",
        "Workspace endpoint: https://<workspace-host>/oidc/v1/authorize",
    ]
    token_endpoint_help: ClassVar[str | list[str] | None] = [
        "Replace {databricks-instance} with your workspace hostname.",
        "Workspace endpoint: https://<workspace-host>/oidc/v1/token",
    ]

    def _use_pkce(self) -> bool:
        return True


class DatabricksCCProvider(ClientCredentialsOAuthProvider):
    """Databricks machine-to-machine OAuth provider."""

    id: ClassVar[str] = "databricks"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(default=DATABRICKS_SERVICE_SCOPES)
    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="databricks",
        name="Databricks (Service principal)",
        description=(
            "Authenticate to Databricks workspace APIs with a service principal."
        ),
        requires_config=True,
        enabled=True,
        api_docs_url="https://docs.databricks.com/api/workspace/introduction",
        setup_guide_url=("https://docs.databricks.com/aws/en/dev-tools/auth/oauth-m2m"),
        troubleshooting_url=DATABRICKS_AUTH_DOCS_URL,
    )
    default_authorization_endpoint: ClassVar[str | None] = (
        DATABRICKS_AUTHORIZATION_ENDPOINT
    )
    default_token_endpoint: ClassVar[str | None] = DATABRICKS_TOKEN_ENDPOINT
    authorization_endpoint_help: ClassVar[str | list[str] | None] = [
        "Unused for service authentication. Keep it on the same workspace as the token endpoint.",
    ]
    token_endpoint_help: ClassVar[str | list[str] | None] = [
        "Replace {databricks-instance} with your workspace hostname.",
        "Workspace endpoint: https://<workspace-host>/oidc/v1/token",
    ]
