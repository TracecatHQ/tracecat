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

ALERTS_ENDPOINT = "/web/api/v2.1/cloud-detection/alerts"
ANALYST_VERDICT_ENDPOINT = "/web/api/v2.1/cloud-detection/alerts/analyst-verdict"

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


@registry.register(
    default_title="Suspend Okta user",
    description="Suspend an Okta user",
    display_group="Okta",
    namespace="integrations.okta",
    secrets=[okta_secret],
)
async def suspend_okta_user(
    username: Annotated[
        str,
        Field(..., description="Username to suspend"),
    ],
) -> bool:
    api_token = os.getenv("OKTA_API_TOKEN")
    base_url = os.getenv("OKTA_BASE_URL")
    headers = {
        "Authorization": f"SSWS {api_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{base_url}/api/v1/users/${username}/lifecycle/suspend",
            headers=headers,
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
    username: Annotated[
        str,
        Field(..., description="Username to unsuspend"),
    ],
) -> bool:
    api_token = os.getenv("OKTA_API_TOKEN")
    base_url = os.getenv("OKTA_BASE_URL")
    headers = {
        "Authorization": f"SSWS {api_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{base_url}/api/v1/users/${username}/lifecycle/unsuspend",
            headers=headers,
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
    username: Annotated[
        str,
        Field(..., description="Username for whom to expire sessions"),
    ],
) -> bool:
    api_token = os.getenv("OKTA_API_TOKEN")
    base_url = os.getenv("OKTA_BASE_URL")
    headers = {
        "Authorization": f"SSWS {api_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient() as client:
        response = await client.delete(
            f"{base_url}/api/v1/users/{username}/sessions",
            headers=headers,
        )
        response.raise_for_status()
        return True
