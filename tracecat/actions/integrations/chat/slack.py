"""Slack ChatOps integration.

Authentication method: OAuth 2.0

Secret Values:
```json
{
    "bot_token": <bot_token>,
    "app_token": <app-level-token>,(optional)
    "signing_secret": <signing-secret> (optional)
}
```

Supported APIs:
```python
post_message = {
    "endpoint": "client.chat_postMessage",
    "user_agent": "bolt-python",
    "app_scopes": ["chat:write"]
    "reference": "https://api.slack.com/methods/chat.postMessage",
}

list_users = {
    "endpoint": "client.users_list",
    "user_agent": "bolt-python",
    "app_scopes": ["users:read", "users:read.email"]
    "reference": "https://api.slack.com/methods/users.list",
}
```

Note: Slack accepts more complex message payloads using [Blocks](https://app.slack.com/block-kit-builder).
"""

import os
from typing import Annotated, Any

from slack_sdk.web.async_client import AsyncWebClient

from tracecat.registry import Field, registry
from tracecat.types.exceptions import TracecatCredentialsError


# MESSAGES API
@registry.register(
    default_title="Post Slack Message",
    description="Send Slack message to channel.",
    display_group="ChatOps",
    namespace="integrations.chat.slack",
    secrets=["slack"],
)
async def post_slack_message(
    channel: Annotated[
        str, Field(..., description="The Slack channel ID to send a message to")
    ],
    text: Annotated[str | None, Field(description="The message text")] = None,
    blocks: Annotated[
        list[dict[str, Any]] | None, Field(description="Slack blocks definition")
    ] = None,
) -> dict[str, Any]:
    if (bot_token := os.environ.get("SLACK_BOT_TOKEN")) is None:
        raise TracecatCredentialsError("Credential `slack.SLACK_BOT_TOKEN` is not set")
    client = AsyncWebClient(token=bot_token)

    # Exiting the TaskGroup block will automatically gather all tasks
    result = await client.chat_postMessage(
        channel=channel,
        text=text,
        blocks=blocks,
    )
    return result["message"]


# USERS API
@registry.register(
    default_title="List Slack Users",
    description="Fetch Slack users by team ID or email.",
    display_group="ChatOps",
    inamespace="integrations.chat.slack",
    secrets=["slack"],
)
async def list_slack_users(
    team_id: Annotated[
        str | None,
        Field(default=None, description="The Slack team ID to filter users by"),
    ] = None,
    email: Annotated[
        str | None, Field(default=None, description="The email to filter users by")
    ] = None,
) -> list[dict[str, str]]:
    if (bot_token := os.environ.get("SLACK_BOT_TOKEN")) is None:
        raise TracecatCredentialsError("Credential `slack.SLACK_BOT_TOKEN` is not set")
    client = AsyncWebClient(token=bot_token)
    users = []
    # NOTE: Slack client API calls returns a AsyncSlackResponse,
    # which is an async generator that yields pages of results
    async for page in await client.users_list(team_id=team_id):
        users.extend(page["members"])

    if email:
        # Filter for users with matching email
        users = [user for user in users if user["profile"]["email"] == email]

    return users
