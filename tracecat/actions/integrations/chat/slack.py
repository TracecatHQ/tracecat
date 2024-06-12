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

from __future__ import annotations

import asyncio
from typing import Any

from slack_sdk.web.async_client import AsyncSlackResponse, AsyncWebClient
from uim.schemas.messages import SlackMessage


# MESSAGES API
async def post_slack_messages(
    bot_token: str,
    channel: str,
    messages: list[SlackMessage],
) -> list[dict[str, Any]]:
    client = AsyncWebClient(token=bot_token)
    tasks: list[asyncio.Task[AsyncSlackResponse]] = []
    async with asyncio.TaskGroup() as tg:
        for msg in messages:
            task = client.chat_postMessage(
                channel=channel,
                text=msg.text,
                blocks=msg.blocks,
            )
            tasks.append(tg.create_task(task))

    # Exiting the TaskGroup block will automatically gather all tasks
    return [task.result()["message"] for task in tasks]


# USERS API
async def list_slack_users(
    bot_token: str,
    team_id: str | None = None,
    email: str | None = None,
) -> list[dict[str, str]]:
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
