"""Okta integration.

Authentication method: Token

Requires: A secret named `okta` with the following keys:
- `OKTA_BASE_URL`
- `OKTA_API_TOKEN`

"""

import os
from datetime import datetime
from typing import Annotated, Any

import httpx

from tracecat.registry import Field, RegistrySecret, registry

okta_secret = RegistrySecret(
    name="okta",
    keys=["OKTA_BASE_URL", "OKTA_API_TOKEN"],
)
"""Okta secret.

- name: `okta`
- keys:
    - `OKTA_BASE_URL`
    - `OKTA_API_TOKEN`
"""


def create_okta_client() -> httpx.AsyncClient:
    OKTA_API_TOKEN = os.getenv("OKTA_API_TOKEN")
    if OKTA_API_TOKEN is None:
        raise ValueError("OKTA_API_TOKEN is not set")
    client = httpx.AsyncClient(
        base_url=f'{os.getenv("OKTA_BASE_URL")}/api/v1',
        headers={
            "Authorization": f"SSWS {OKTA_API_TOKEN}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    return client


@registry.register(
    default_title="Find Okta Users",
    description="Find Okta users by login username or email",
    display_group="Okta",
    namespace="integrations.okta",
    secrets=[okta_secret],
)
async def find_okta_users(
    username_or_email: Annotated[
        str,
        Field(..., description="Login username or e-mail to find"),
    ],
) -> list:
    async with create_okta_client() as client:
        params = {
            "search": (
                f'profile.login eq "{username_or_email}" or profile.email eq "{username_or_email}"'
            )
        }
        response = await client.get(
            "/users",
            params=params,
        )

        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Suspend Okta user",
    description="Suspend an Okta user",
    display_group="Okta",
    namespace="integrations.okta",
    secrets=[okta_secret],
)
async def suspend_okta_user(
    okta_user_id: Annotated[
        str,
        Field(..., description="Okta user id to suspend"),
    ],
) -> bool:
    async with create_okta_client() as client:
        response = await client.post(
            f"/users/{okta_user_id}/lifecycle/suspend",
        )
        response.raise_for_status()
        return True


@registry.register(
    default_title="Unsuspend Okta user",
    description="Unsuspend an Okta user",
    display_group="Okta",
    namespace="integrations.okta",
    secrets=[okta_secret],
)
async def unsuspend_okta_user(
    okta_user_id: Annotated[
        str,
        Field(..., description="Okta user id to unsuspend"),
    ],
) -> bool:
    async with create_okta_client() as client:
        response = await client.post(
            f"/users/{okta_user_id}/lifecycle/unsuspend",
        )
        response.raise_for_status()
        return True


@registry.register(
    default_title="Expire Okta sessions",
    description="Expire current Okta sessions for a user",
    display_group="Okta",
    namespace="integrations.okta",
    secrets=[okta_secret],
)
async def expire_okta_sessions(
    okta_user_id: Annotated[
        str,
        Field(..., description="Okta user id to expire sessions for"),
    ],
) -> bool:
    async with create_okta_client() as client:
        response = await client.delete(
            f"/users/{okta_user_id}/sessions",
        )
        response.raise_for_status()
        return True


@registry.register(
    default_title="List Okta user events",
    description="List Okta events for a user",
    display_group="Okta",
    namespace="integrations.okta",
    secrets=[okta_secret],
)
async def list_okta_user_events(
    okta_user_id: Annotated[
        str,
        Field(..., description="Okta user id to list events for."),
    ],
    start_time: Annotated[
        datetime,
        Field(..., description="Start time, return alerts created after this time."),
    ],
    end_time: Annotated[
        datetime,
        Field(..., description="End time, return alerts created before this time."),
    ],
    limit: Annotated[
        int, Field(default=1000, description="Maximum number of alerts to return.")
    ] = 1000,
) -> list[dict[str, Any]]:
    async with create_okta_client() as client:
        params = {
            "filter": (f'actor.id eq "{okta_user_id}"'),
            "since": (start_time.strftime("%Y-%m-%dT%H:%M:%S")),
            "until": (end_time.strftime("%Y-%m-%dT%H:%M:%S")),
            "limit": limit,
        }
        response = await client.get(
            "/logs",
            params=params,
        )
        response.raise_for_status()
        return response.json()
