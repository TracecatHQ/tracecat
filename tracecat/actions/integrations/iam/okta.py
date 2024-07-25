"""Okta integration.

Authentication method: Token

Requires: A secret named `okta` with the following keys:
- `OKTA_BASE_URL`
- `OKTA_API_TOKEN`

"""

import os
from typing import Annotated

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
        base_url=os.getenv("OKTA_BASE_URL"),
        headers={
            "Authorization": f"SSWS {OKTA_API_TOKEN}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    return client


@registry.register(
    default_title="Find Okta User",
    description="Find an Okta user by login username or email",
    display_group="Okta",
    namespace="integrations.okta",
    secrets=[okta_secret],
)
async def find_okta_user(
    username_or_email: Annotated[
        str,
        Field(..., description="Login username or e-mail to find"),
    ],
) -> list:
    async with create_okta_client() as client:
        params = [
            "search",
            f"profile.login%20eq%20%22{username_or_email}%22%20or%20profile.email%20eq%20%22{username_or_email}%22",
        ]
        response = await client.get(
            f"{client.base_url}/api/v1/users?search=profile.login%20eq%20%22{username_or_email}%22%20or%20profile.email%20eq%20%22{username_or_email}%22",
            headers=client.headers,
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
            f"{client.base_url}/api/v1/users/{okta_user_id}/lifecycle/suspend",
            headers=client.headers,
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
            f"{client.base_url}/api/v1/users/{okta_user_id}/lifecycle/unsuspend",
            headers=client.headers,
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
            f"{client.base_url}/api/v1/users/{okta_user_id}/sessions",
            headers=client.headers,
        )
        response.raise_for_status()
        return True
