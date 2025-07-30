"""Wazuh API Authentication."""

from typing import Annotated

import httpx
from pydantic import Field

from tracecat_registry import RegistrySecret, registry, secrets

wazuh_secret = RegistrySecret(
    name="wazuh_wui",
    keys=["WAZUH_WUI_USERNAME", "WAZUH_WUI_PASSWORD"],
)
"""Wazuh API credentials.

- name: `wazuh_wui`
- keys:
    - `WAZUH_WUI_USERNAME`
    - `WAZUH_WUI_PASSWORD`
"""


@registry.register(
    default_title="Get access token",
    description="Authenticate with the Wazuh API and retrieve a token.",
    display_group="Wazuh",
    doc_url="https://documentation.wazuh.com/current/user-manual/api/reference.html#tag/Security",
    namespace="tools.wazuh",
    secrets=[wazuh_secret],
)
async def get_access_token(
    url: Annotated[str, Field(..., description="Base URL for Wazuh WUI API.")],
    verify_ssl: Annotated[
        bool,
        Field(
            True,
            description="If False, disables SSL verification for internal networks.",
        ),
    ],
    auth_token_exp_timeout: Annotated[
        int, Field(900, description="Change the token base duration")
    ],
) -> str:
    async with httpx.AsyncClient(verify=verify_ssl) as client:
        response = await client.post(
            f"{url}/security/user/authenticate",
            headers={"Content-Type": "application/json"},
            json={"auth_token_exp_timeout": auth_token_exp_timeout},
            auth=(secrets.get("WAZUH_WUI_USERNAME"), secrets.get("WAZUH_WUI_PASSWORD")),
        )
        response.raise_for_status()
        return response.json()["data"]["token"]
