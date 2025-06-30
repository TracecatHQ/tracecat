"""Microsoft Graph authentication via MSAL Python library.

Currently supports confidential app-only authentication (i.e. `acquire_token_for_client` method)
"""

from typing import Annotated, Any

import httpx
from msal import ConfidentialClientApplication
from pydantic import Doc
from tracecat import __version__

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
        Doc(
            ...,
            description='Microsoft Graph scopes, defaults to ["https://graph.microsoft.com/.default"].',
        ),
    ] = None,
    authority: Annotated[
        str | None,
        Doc(
            ...,
            description='Microsoft Graph authority, defaults to "https://login.microsoftonline.com/common".',
        ),
    ] = None,
    oidc_authority: Annotated[
        str | None,
        Doc(
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


microsoft_oauth_secret = RegistrySecret.oauth("microsoft")
"""Microsoft Graph OAuth2.0 credentials.

- name: `microsoft`
- provider_id: `microsoft`
usage:
MICROSOFT_ACCESS_TOKEN
"""


@registry.register(
    default_title="Send Teams message",
    description="Send a message to a Microsoft Teams channel.",
    display_group="Microsoft Graph",
    doc_url="https://learn.microsoft.com/en-us/graph/api/channel-post-messages",
    namespace="tools.microsoft_graph",
    secrets=[microsoft_oauth_secret],
)
async def send_teams_message(
    team_id: Annotated[
        str, Doc(..., description="The ID of the team to send the message to.")
    ],
    channel_id: Annotated[
        str, Doc(..., description="The ID of the channel to send the message to.")
    ],
    message: Annotated[str, Doc(..., description="The message to send.")],
) -> dict[str, str]:
    token = secrets.get("MICROSOFT_ACCESS_TOKEN")

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # Microsoft Graph API endpoint for sending channel messages
    url = f"https://graph.microsoft.com/v1.0/teams/{team_id}/channels/{channel_id}/messages"

    # Message payload
    payload = {"body": {"content": message}}

    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Create Teams channel",
    description="Create a new channel in a Microsoft Teams team. Can create either public (standard) or private channels.",
    display_group="Microsoft Graph",
    doc_url="https://learn.microsoft.com/en-us/graph/api/channel-post",
    namespace="tools.microsoft_graph",
    secrets=[microsoft_oauth_secret],
)
async def create_teams_channel(
    team_id: Annotated[
        str, Doc(..., description="The ID of the team to create the channel in.")
    ],
    display_name: Annotated[
        str, Doc(..., description="The display name for the channel.")
    ],
    description: Annotated[
        str | None, Doc(None, description="Description for the channel.")
    ] = None,
    is_private: Annotated[
        bool,
        Doc(
            False, description="Whether to create a private channel (requires members)."
        ),
    ] = False,
    owner_user_ids: Annotated[
        list[str] | None,
        Doc(
            None,
            description="List of user IDs to add as owners (required for private channels).",
        ),
    ] = None,
) -> dict[str, str]:
    """Create a Teams channel.

    For private channels, at least one owner must be specified in owner_user_ids.
    """
    token = secrets.get("MICROSOFT_ACCESS_TOKEN")

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    url = f"https://graph.microsoft.com/beta/teams/{team_id}/channels"

    if is_private:
        if not owner_user_ids:
            raise ValueError(
                "Private channels require at least one owner in owner_user_ids"
            )

        members = []
        for user_id in owner_user_ids:
            members.append(
                {
                    "@odata.type": "#microsoft.graph.aadUserConversationMember",
                    "roles": ["owner"],
                    "user@odata.bind": f"https://graph.microsoft.com/beta/users('{user_id}')",
                }
            )

        payload = {
            "@odata.type": "#Microsoft.Graph.channel",
            "membershipType": "private",
            "displayName": display_name,
            "description": description or "",
            "members": members,
        }
    else:
        payload = {
            "displayName": display_name,
            "description": description or "",
            "membershipType": "standard",
        }

    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="List channel messages",
    description="Retrieve the list of messages (without the replies) in a channel of a team.",
    display_group="Microsoft Graph",
    doc_url="https://learn.microsoft.com/en-us/graph/api/channel-list-messages?view=graph-rest-beta&tabs=python",
    namespace="tools.microsoft_graph",
    secrets=[microsoft_oauth_secret],
)
async def list_channel_messages(
    team_id: Annotated[
        str, Doc(..., description="The ID of the team containing the channel.")
    ],
    channel_id: Annotated[
        str, Doc(..., description="The ID of the channel to list messages from.")
    ],
    top: Annotated[
        int | None,
        Doc(
            None,
            description="Number of messages to return per page (default 20, max 50).",
        ),
    ] = None,
    expand_replies: Annotated[
        bool, Doc(False, description="Whether to expand replies for each message.")
    ] = False,
) -> dict[str, Any]:
    """List messages from a Teams channel.

    Note: This API requires ChannelMessage.Read.All or ChannelMessage.Read.Group permissions.
    """
    token = secrets.get("MICROSOFT_ACCESS_TOKEN")

    headers = {"Authorization": f"Bearer {token}"}

    url = f"https://graph.microsoft.com/beta/teams/{team_id}/channels/{channel_id}/messages"

    params = {}
    if top:
        if top > 50:
            raise ValueError("Top parameter cannot exceed 50 messages per page")
        params["$top"] = top

    if expand_replies:
        params["$expand"] = "replies"

    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers, params=params)
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Get user ID by email",
    description="Get a user's ID by searching for their email address in mail or userPrincipalName Docs.",
    display_group="Microsoft Graph",
    doc_url="https://learn.microsoft.com/en-us/graph/api/user-list?view=graph-rest-beta&tabs=http",
    namespace="tools.microsoft_graph",
    secrets=[microsoft_oauth_secret],
)
async def get_user_id_by_email(
    email: Annotated[str, Doc(..., description="The email address to search for.")],
) -> dict[str, str]:
    """Get a user's ID by email address.

    Note: This API requires User.ReadBasic.All, User.Read.All, or Directory.Read.All permissions.
    """
    token = secrets.get("MICROSOFT_ACCESS_TOKEN")

    headers = {"Authorization": f"Bearer {token}"}

    url = "https://graph.microsoft.com/beta/users"

    filter_query = f"mail eq '{email}'"

    params = {"$filter": filter_query, "$select": "id", "$top": "1"}

    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers, params=params)
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Delete Teams channel",
    description="Delete a channel from a Microsoft Teams team.",
    display_group="Microsoft Graph",
    doc_url="https://learn.microsoft.com/en-us/graph/api/channel-delete?view=graph-rest-beta&tabs=http",
    namespace="tools.microsoft_graph",
    secrets=[microsoft_oauth_secret],
)
async def delete_teams_channel(
    team_id: Annotated[
        str, Doc(..., description="The ID of the team containing the channel.")
    ],
    channel_id: Annotated[
        str, Doc(..., description="The ID of the channel to delete.")
    ],
) -> dict[str, Any]:
    """Delete a Teams channel.

    Note: This API requires Channel.Delete.All or Channel.Delete.Group permissions.
    """
    token = secrets.get("MICROSOFT_ACCESS_TOKEN")

    headers = {"Authorization": f"Bearer {token}"}

    url = f"https://graph.microsoft.com/beta/teams/{team_id}/channels/{channel_id}"

    async with httpx.AsyncClient() as client:
        response = await client.delete(url, headers=headers)
        response.raise_for_status()

        return {
            "success": True,
            "status_code": response.status_code,
            "team_id": team_id,
            "channel_id": channel_id,
            "message": "Channel deleted successfully",
        }
