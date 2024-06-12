"""Unified API for users management.

Supported Capablities:
- `list_users`: no common input schema implemented yet.
- `lookup_user_by_email`: `email` required.
"""

import os

from tracecat.actions.integrations import get_capability


async def list_chat_users(
    team_id: str | None, email: str | None, vendor: str
) -> list[dict]:
    secret = os.getenv(vendor=vendor)
    list_vendor_users = get_capability(
        category="chat", capability="list_users", vendor=vendor
    )
    users = await list_vendor_users(team_id=team_id, email=email, **secret)
    return users
