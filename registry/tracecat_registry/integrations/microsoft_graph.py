"""Microsoft Graph authentication via MSAL Python library.

Currently supports confidential app-only authentication (i.e. `acquire_token_for_client` method)
"""

from typing import Annotated, Any

import httpx
from msal import ConfidentialClientApplication
from pydantic import Field
from tracecat import __version__
from tracecat.logger import logger

from tracecat_registry import RegistrySecret, registry, secrets

microsoft_graph_secret = RegistrySecret(
    name="microsoft_graph",
    keys=[
        "MICROSOFT_GRAPH_CLIENT_ID",
        "MICROSOFT_GRAPH_CLIENT_SECRET",
    ],
)
"""Microsoft Graph OAuth2.0 credentials.

- name: `microsoft_graph`
- keys:
    - `MICROSOFT_GRAPH_CLIENT_ID`
    - `MICROSOFT_GRAPH_CLIENT_SECRET`
"""


@registry.register(
    default_title="Get access token",
    description="Retrieve a JWT token for Microsoft Graph API calls from a confidential application.",
    display_group="Microsoft Graph",
    doc_url="https://msal-python.readthedocs.io/en/latest/#confidentialclientapplication",
    namespace="tools.microsoft_graph",
    secrets=[microsoft_graph_secret],
)
def get_access_token(
    scopes: Annotated[
        list[str] | None,
        Field(
            ...,
            description='Microsoft Graph scopes, defaults to ["https://graph.microsoft.com/.default"].',
        ),
    ] = None,
    authority: Annotated[
        str | None,
        Field(
            ...,
            description='Microsoft Graph authority, defaults to "https://login.microsoftonline.com/common".',
        ),
    ] = None,
    oidc_authority: Annotated[
        str | None,
        Field(
            ...,
            description='Microsoft Graph OIDC authority, defaults to "https://login.microsoftonline.com/common".',
        ),
    ] = None,
) -> str:
    client_id = secrets.get("MICROSOFT_GRAPH_CLIENT_ID")
    client_secret = secrets.get("MICROSOFT_GRAPH_CLIENT_SECRET")
    scopes = scopes or ["https://graph.microsoft.com/.default"]
    authority = authority or "https://login.microsoftonline.com/common"
    app = ConfidentialClientApplication(
        client_id=client_id,
        client_credential=client_secret,
        authority=authority,
        oidc_authority=oidc_authority,
        app_name="tracecat",
        app_version=__version__,
    )
    result = app.acquire_token_for_client(scopes=scopes)
    if result is None:
        raise ValueError("Failed to acquire token. Empty result returned.")
    elif "access_token" in result:
        return result["access_token"]
    else:
        raise ValueError(f"Failed to acquire token: {result}")


microsoft_oauth_secret = RegistrySecret.oauth("microsoft_cc")
"""Microsoft Graph OAuth2.0 credentials.

- name: `microsoft`
- provider_id: `microsoft`
usage:
MICROSOFT_ACCESS_TOKEN
"""


@registry.register(
    default_title="Read teams messages",
    description="Read messages from a Microsoft Teams channel using Microsoft Graph.",
    display_group="Microsoft Graph",
    doc_url="https://learn.microsoft.com/en-us/graph/api/channel-list-messages",
    namespace="tools.microsoft_graph",
    secrets=[microsoft_oauth_secret],
)
async def read_teams_channel(
    team_id: Annotated[
        str,
        Field(..., description="The ID of the Microsoft Teams team."),
    ],
    channel_id: Annotated[
        str,
        Field(..., description="The ID of the channel within the team."),
    ],
    limit: Annotated[
        int,
        Field(
            50,
            description="Maximum number of messages to retrieve (default: 50).",
            ge=1,
            le=999,
        ),
    ] = 50,
    expand_replies: Annotated[
        bool,
        Field(
            False,
            description="Whether to expand replies to messages.",
        ),
    ] = False,
) -> dict[str, Any]:
    """Read messages from a Microsoft Teams channel."""
    token = secrets.get("MICROSOFT_CC_ACCESS_TOKEN")
    logger.info("Reading Teams channel messages using Microsoft Graph", token=token)

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # Microsoft Graph API endpoint for reading channel messages
    url = f"https://graph.microsoft.com/v1.0/teams/{team_id}/channels/{channel_id}/messages"

    # Build query parameters
    params = {
        "$top": limit,
    }

    logger.info(f"Reading messages from Teams channel {channel_id} in team {team_id}")
    logger.debug(f"Request URL: {url}")
    logger.debug(f"Query parameters: {params}")

    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers, params=params)
        response.raise_for_status()
        return response.json()
