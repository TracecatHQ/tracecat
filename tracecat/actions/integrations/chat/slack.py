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
    "user_agent": "slack-python",
    "app_scopes": ["chat:write"]
    "reference": "https://api.slack.com/methods/chat.postMessage",
}

list_users = {
    "endpoint": "client.users_list",
    "user_agent": "slack-python",
    "app_scopes": ["channels:history", "groups:history", "im:history", "mpim:history"]
    "reference": "https://api.slack.com/methods/users.list",
}

list_conversations = {
    "endpoint": "client.conversations_history",
    "user_agent": "slack-python",
    "app_scopes": ["conversations:history"]
    "reference": "https://api.slack.com/methods/conversations.history",
}
```

Note: Slack accepts more complex message payloads using [Blocks](https://app.slack.com/block-kit-builder).
"""

import os
from datetime import datetime
from typing import Annotated, Any

from slack_sdk.web.async_client import AsyncWebClient

from tracecat.registry import Field, RegistrySecret, registry
from tracecat.types.exceptions import TracecatCredentialsError

slack_secret = RegistrySecret(name="slack", keys=["SLACK_BOT_TOKEN"])
"""Slack secret.

- name: `slack`
- keys:
    - `SLACK_BOT_TOKEN`
"""


# MESSAGES API
@registry.register(
    default_title="Post Slack Message",
    description="Send Slack message to channel.",
    display_group="ChatOps",
    namespace="integrations.chat.slack",
    secrets=[slack_secret],
)
async def post_slack_message(
    channel: Annotated[
        str, Field(..., description="The Slack channel ID to send a message to")
    ],
    thread_ts: Annotated[
        str | None,
        Field(
            default=None,
            description="The timestamp of the parent message. Used to create a thread.",
        ),
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
        thread_ts=thread_ts,
        text=text,
        blocks=blocks,
    )
    return result["message"]


# CONVERSATIONS API
@registry.register(
    default_title="List Slack Conversation History",
    description="Fetch past messages from a Slack channel.",
    display_group="ChatOps",
    namespace="integrations.chat.slack",
    secrets=[slack_secret],
)
async def list_slack_conversations(
    channel: Annotated[
        str,
        Field(
            ..., description="The Slack channel ID to fetch the message history from"
        ),
    ],
    limit: Annotated[
        int | None,
        Field(default=100, description="The maximum number of messages to retrieve"),
    ] = 100,
    latest: Annotated[
        datetime | None,
        Field(
            default=None,
            description="End of time range of messages to include in results",
        ),
    ] = None,
    oldest: Annotated[
        datetime | None,
        Field(
            default=None,
            description="Start of time range of messages to include in results",
        ),
    ] = None,
) -> list[dict[str, Any]]:
    if (bot_token := os.environ.get("SLACK_BOT_TOKEN")) is None:
        raise TracecatCredentialsError("Credential `slack.SLACK_BOT_TOKEN` is not set")
    client = AsyncWebClient(token=bot_token)

    result = await client.conversations_history(
        channel=channel,
        limit=limit,
        latest=latest.isoformat(),
        oldest=oldest.isoformat(),
    )
    return result["messages"]


# USERS API
@registry.register(
    default_title="List Slack Users",
    description="Fetch Slack users by team ID or list of emails.",
    display_group="ChatOps",
    namespace="integrations.chat.slack",
    secrets=[slack_secret],
)
async def list_slack_users(
    team_id: Annotated[
        str | None,
        Field(default=None, description="The Slack team ID to filter users by"),
    ] = None,
    emails: Annotated[
        list[str] | None,
        Field(default=None, description="List of emails to filter users by"),
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

    if emails:
        # Filter for users with matching email
        filter_by = set(emails)
        users = [user for user in users if user["profile"].get("email") in filter_by]
    return users
