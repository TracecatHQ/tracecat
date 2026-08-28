"""Generic Snowflake SQL REST API client."""

from typing import Annotated, Any, TypedDict, cast

import httpx
from pydantic import Field

from tracecat_registry import RegistryOAuthSecret, RegistrySecret, registry, secrets

snowflake_user_oauth_secret = RegistryOAuthSecret(
    provider_id="snowflake_sql",
    grant_type="authorization_code",
    optional=True,
)
"""Snowflake user OAuth credentials."""

snowflake_service_oauth_secret = RegistryOAuthSecret(
    provider_id="snowflake_sql",
    grant_type="client_credentials",
    optional=True,
)
"""Snowflake service OAuth credentials."""

snowflake_sql_secret = RegistrySecret(
    name="snowflake_sql",
    keys=[
        "SNOWFLAKE_OAUTH_CLIENT_ID",
        "SNOWFLAKE_OAUTH_CLIENT_SECRET",
        "SNOWFLAKE_OAUTH_TOKEN_URL",
    ],
    optional_keys=[
        "SNOWFLAKE_OAUTH_SCOPE",
        "SNOWFLAKE_OAUTH_AUDIENCE",
        "SNOWFLAKE_OAUTH_TOKEN_ENDPOINT_AUTH_METHOD",
    ],
    optional=True,
)
"""Stored Snowflake SQL API OAuth service credentials.

- name: `snowflake_sql`
- keys:
    - `SNOWFLAKE_OAUTH_CLIENT_ID`
    - `SNOWFLAKE_OAUTH_CLIENT_SECRET`
    - `SNOWFLAKE_OAUTH_TOKEN_URL`
- optional_keys:
    - `SNOWFLAKE_OAUTH_SCOPE`
    - `SNOWFLAKE_OAUTH_AUDIENCE`
    - `SNOWFLAKE_OAUTH_TOKEN_ENDPOINT_AUTH_METHOD`

Configure the client ID, client secret, external identity-provider token URL,
and optional scope or audience used to mint a Snowflake-compatible OAuth token.
Token endpoint authentication defaults to `client_secret_post`; set it to
`client_secret_basic` when required by the identity provider.
"""


class SnowflakeSQLResponse(TypedDict):
    """Snowflake SQL API HTTP response envelope."""

    status_code: int
    headers: dict[str, str]
    # The generic API wrapper cannot know the schema of every Snowflake response.
    data: str | dict[str, Any] | list[Any] | None


async def _mint_stored_service_token() -> str:
    data = {"grant_type": "client_credentials"}
    if scope := secrets.get_or_default("SNOWFLAKE_OAUTH_SCOPE"):
        data["scope"] = scope
    if audience := secrets.get_or_default("SNOWFLAKE_OAUTH_AUDIENCE"):
        data["audience"] = audience

    client_id = secrets.get("SNOWFLAKE_OAUTH_CLIENT_ID")
    client_secret = secrets.get("SNOWFLAKE_OAUTH_CLIENT_SECRET")
    if (
        secrets.get_or_default(
            "SNOWFLAKE_OAUTH_TOKEN_ENDPOINT_AUTH_METHOD",
            "client_secret_post",
        )
        == "client_secret_basic"
    ):
        auth = httpx.BasicAuth(client_id, client_secret)
    else:
        auth = None
        data["client_id"] = client_id
        data["client_secret"] = client_secret

    async with httpx.AsyncClient() as client:
        token_url = secrets.get("SNOWFLAKE_OAUTH_TOKEN_URL")
        if auth:
            response = await client.post(token_url, auth=auth, data=data)
        else:
            response = await client.post(token_url, data=data)
    response.raise_for_status()
    return cast(str, response.json()["access_token"])


async def _get_oauth_token() -> str:
    if token := secrets.get_or_default(snowflake_user_oauth_secret.token_name):
        return token
    if token := secrets.get_or_default(snowflake_service_oauth_secret.token_name):
        return token
    return await _mint_stored_service_token()


@registry.register(
    default_title="Call API",
    description="Call a Snowflake SQL REST API endpoint.",
    display_group="Snowflake SQL API",
    doc_url="https://docs.snowflake.com/en/developer-guide/sql-api/index",
    namespace="tools.snowflake_sql",
    secrets=[
        snowflake_user_oauth_secret,
        snowflake_service_oauth_secret,
        snowflake_sql_secret,
    ],
)
async def call_api(
    url: Annotated[
        str,
        Field(..., description="Full Snowflake SQL REST API URL."),
    ],
    method: Annotated[
        str,
        Field(..., description="HTTP method for the Snowflake SQL API request."),
    ],
    params: Annotated[
        dict[str, Any] | None,
        Field(..., description="Query parameters for the Snowflake API method."),
    ] = None,
    payload: Annotated[
        dict[str, Any] | None,
        Field(..., description="JSON request body for the Snowflake API method."),
    ] = None,
    timeout: Annotated[
        float | None,
        Field(
            ...,
            description="Request timeout in seconds. Set to null to disable it.",
        ),
    ] = 60.0,
) -> SnowflakeSQLResponse:
    """Call a Snowflake SQL API endpoint and return its HTTP response."""
    token = await _get_oauth_token()
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "Tracecat-Snowflake-SQL-API/1.0",
        "Authorization": f"Bearer {token}",
        "X-Snowflake-Authorization-Token-Type": "OAUTH",
    }

    request_params = (
        {key: value for key, value in params.items() if value is not None}
        if params
        else None
    )
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.request(
            method=method,
            url=url,
            headers=headers,
            params=request_params,
            json=payload,
        )
    response.raise_for_status()
    return SnowflakeSQLResponse(
        status_code=response.status_code,
        headers=dict(response.headers.items()),
        data=response.json() if response.content else None,
    )
