"""Snowflake OAuth providers for REST API access."""

from typing import ClassVar

from tracecat.integrations.providers.base import (
    AuthorizationCodeOAuthProvider,
    ClientCredentialsOAuthProvider,
)
from tracecat.integrations.schemas import ProviderMetadata, ProviderScopes

SNOWFLAKE_AUTHORIZATION_ENDPOINT = "https://{snowflake-account-host}/oauth/authorize"
SNOWFLAKE_TOKEN_ENDPOINT = "https://{snowflake-account-host}/oauth/token-request"
SNOWFLAKE_OAUTH_DOCS_URL = "https://docs.snowflake.com/en/user-guide/oauth-custom"
SNOWFLAKE_EXTERNAL_OAUTH_TOKEN_ENDPOINT = "https://{identity-provider}/oauth/token"


class SnowflakeACProvider(AuthorizationCodeOAuthProvider):
    """Snowflake user OAuth provider."""

    id: ClassVar[str] = "snowflake"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(default=["refresh_token"])
    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="snowflake",
        name="Snowflake (User OAuth)",
        description="Connect a Snowflake user with OAuth to call its REST APIs.",
        requires_config=True,
        enabled=True,
        api_docs_url=(
            "https://docs.snowflake.com/en/developer-guide/snowflake-rest-api/"
            "snowflake-rest-api"
        ),
        setup_guide_url=SNOWFLAKE_OAUTH_DOCS_URL,
        troubleshooting_url=(
            "https://docs.snowflake.com/en/developer-guide/snowflake-rest-api/"
            "authentication"
        ),
    )
    default_authorization_endpoint: ClassVar[str | None] = (
        SNOWFLAKE_AUTHORIZATION_ENDPOINT
    )
    default_token_endpoint: ClassVar[str | None] = SNOWFLAKE_TOKEN_ENDPOINT
    authorization_endpoint_help: ClassVar[str | list[str] | None] = [
        "Replace {snowflake-account-host} with your Snowflake account hostname, for example:",
        "https://myorg-myaccount.snowflakecomputing.com/oauth/authorize",
        "The security integration must allow Tracecat's OAuth callback URL.",
    ]
    token_endpoint_help: ClassVar[str | list[str] | None] = [
        "Replace {snowflake-account-host} with your Snowflake account hostname, for example:",
        "https://myorg-myaccount.snowflakecomputing.com/oauth/token-request",
    ]

    def _use_pkce(self) -> bool:
        return True


class SnowflakeCCProvider(ClientCredentialsOAuthProvider):
    """Snowflake service OAuth provider using an external identity provider."""

    id: ClassVar[str] = "snowflake"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(default=[])
    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="snowflake",
        name="Snowflake (Service OAuth)",
        description=(
            "Mint a Snowflake-compatible service token from an external identity "
            "provider using client credentials and configured scopes."
        ),
        requires_config=True,
        enabled=True,
        api_docs_url=(
            "https://docs.snowflake.com/en/developer-guide/snowflake-rest-api/"
            "snowflake-rest-api"
        ),
        setup_guide_url=("https://docs.snowflake.com/en/user-guide/oauth-ext-custom"),
        troubleshooting_url=(
            "https://docs.snowflake.com/en/developer-guide/snowflake-rest-api/"
            "authentication"
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
