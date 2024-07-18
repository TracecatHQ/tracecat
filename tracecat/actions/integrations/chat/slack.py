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

import orjson
from slack_sdk.web.async_client import AsyncWebClient

from tracecat.actions.etl.extraction import extract_emails
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
        latest=str(latest.timestamp()),
        oldest=str(oldest.timestamp()),
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
) -> list[dict[str, str]]:
    if (bot_token := os.environ.get("SLACK_BOT_TOKEN")) is None:
        raise TracecatCredentialsError("Credential `slack.SLACK_BOT_TOKEN` is not set")
    client = AsyncWebClient(token=bot_token)
    users = []
    # NOTE: Slack client API calls returns a AsyncSlackResponse,
    # which is an async generator that yields pages of results
    async for page in await client.users_list(team_id=team_id):
        users.extend(page["members"])
    return users


# Extraction and transformation
@registry.register(
    default_title="Tag Slack users in JSON objects",
    description="Extract emails from list of JSON objects, tags users (if exists), and returns list of JSONs with tagged users.",
    display_group="ChatOps",
    namespace="integrations.chat.slack",
    secrets=[slack_secret],
)
async def tag_slack_users(
    jsons: Annotated[
        list[dict[str, Any]],
        Field(description="List of JSONs to extract emails from and tag Slack users"),
    ],
    team_id: Annotated[
        str | None,
        Field(default=None, description="The Slack team ID to filter users by"),
    ] = None,
) -> list[dict[str, Any]]:
    users = await list_slack_users(team_id=team_id)
    email_to_user = {
        user["profile"]["email"]: user for user in users if user["profile"].get("email")
    }
    tagged_jsons = []
    for json in jsons:
        text = orjson.dumps(json).decode("utf-8")
        emails = extract_emails(texts=[text], normalize=True)
        user_tags = [
            f"<@{email_to_user[email]['id']}>"
            for email in emails
            if email in email_to_user
        ]
        tagged_jsons.append({"json": json, "user_tags": user_tags})

    return tagged_jsons
