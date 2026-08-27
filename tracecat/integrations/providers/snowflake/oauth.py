"""Snowflake OAuth providers for SQL API access."""

from typing import ClassVar

from tracecat.integrations.providers.base import (
    AuthorizationCodeOAuthProvider,
    ClientCredentialsOAuthProvider,
)
from tracecat.integrations.schemas import ProviderMetadata, ProviderScopes

SNOWFLAKE_AUTHORIZATION_ENDPOINT = (
    "https://{snowflake-account}.snowflakecomputing.com/oauth/authorize"
)
SNOWFLAKE_TOKEN_ENDPOINT = (
    "https://{snowflake-account}.snowflakecomputing.com/oauth/token-request"
)
SNOWFLAKE_OAUTH_DOCS_URL = "https://docs.snowflake.com/en/user-guide/oauth-custom"
SNOWFLAKE_EXTERNAL_OAUTH_TOKEN_ENDPOINT = "https://{identity-provider}/oauth/token"


class SnowflakeACProvider(AuthorizationCodeOAuthProvider):
    """Snowflake user OAuth provider."""

    id: ClassVar[str] = "snowflake_sql"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(default=["refresh_token"])
    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="snowflake_sql",
        name="Snowflake SQL API (User OAuth)",
        description="Connect a Snowflake user with OAuth to call the SQL API.",
        requires_config=True,
        enabled=True,
        api_docs_url=("https://docs.snowflake.com/en/developer-guide/sql-api/index"),
        setup_guide_url=SNOWFLAKE_OAUTH_DOCS_URL,
        troubleshooting_url=(
            "https://docs.snowflake.com/en/developer-guide/sql-api/authenticating"
        ),
    )
    default_authorization_endpoint: ClassVar[str | None] = (
        SNOWFLAKE_AUTHORIZATION_ENDPOINT
    )
    default_token_endpoint: ClassVar[str | None] = SNOWFLAKE_TOKEN_ENDPOINT
    authorization_endpoint_help: ClassVar[str | list[str] | None] = [
        "Replace {snowflake-account} with your organization-account hostname.",
        "The security integration must allow Tracecat's OAuth callback URL.",
    ]
    token_endpoint_help: ClassVar[str | list[str] | None] = [
        "Replace {snowflake-account} with your organization-account hostname.",
    ]

    def _use_pkce(self) -> bool:
        return True


class SnowflakeCCProvider(ClientCredentialsOAuthProvider):
    """Snowflake service OAuth provider using an external identity provider."""

    id: ClassVar[str] = "snowflake_sql"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(default=[])
    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="snowflake_sql",
        name="Snowflake SQL API (Service OAuth)",
        description=(
            "Mint a Snowflake-compatible service token from an external identity "
            "provider using client credentials and configured scopes."
        ),
        requires_config=True,
        enabled=True,
        api_docs_url=("https://docs.snowflake.com/en/developer-guide/sql-api/index"),
        setup_guide_url=("https://docs.snowflake.com/en/user-guide/oauth-ext-custom"),
        troubleshooting_url=(
            "https://docs.snowflake.com/en/developer-guide/sql-api/authenticating"
        ),
    )
    # External OAuth only uses the client credentials token endpoint. The base
    # provider requires both fields, so the unused authorization endpoint mirrors it.
    default_authorization_endpoint: ClassVar[str | None] = (
        SNOWFLAKE_EXTERNAL_OAUTH_TOKEN_ENDPOINT
    )
    default_token_endpoint: ClassVar[str | None] = (
        SNOWFLAKE_EXTERNAL_OAUTH_TOKEN_ENDPOINT
    )
    authorization_endpoint_help: ClassVar[str | list[str] | None] = [
        "Unused for service authentication. Keep it matching the token endpoint.",
    ]
    token_endpoint_help: ClassVar[str | list[str] | None] = [
        "Set this to the identity-provider endpoint that accepts the client_credentials grant.",
        "Configure scopes that Snowflake maps to the service user's role.",
        "Identity providers requiring extra token parameters should use stored credentials.",
    ]

    def _get_token_endpoint_auth_method(self) -> str | None:
        return "client_secret_post"
