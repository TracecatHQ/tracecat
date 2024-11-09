"""Limacharlie authentication.

Docs: https://docs.limacharlie.io/apidocs/introduction
"""

import httpx

from tracecat_registry import RegistrySecret, registry, secrets

limacharlie_secret = RegistrySecret(
    name="limacharlie",
    keys=["LIMACHARLIE_SECRET", "LIMACHARLIE_UID"],
    optional_keys=["LIMACHARLIE_OID"],
)
"""Limacharlie secret.

- name: `limacharlie`
- keys:
    - `LIMACHARLIE_SECRET`
    - `LIMACHARLIE_UID`
- optional_keys:
    - `LIMACHARLIE_OID` (organization ID)
"""


@registry.register(
    default_title="Get Limacharlie auth token",
    description="Get an auth token for Limacharlie API calls.",
    display_group="Limacharlie",
    namespace="integrations.limacharlie",
    secrets=[limacharlie_secret],
)
async def get_auth_token() -> str:
    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://jwt.limacharlie.io",
            params={
                "uid": secrets.get("LIMACHARLIE_UID"),
                "secret": secrets.get("LIMACHARLIE_SECRET"),
                "oid": secrets.get("LIMACHARLIE_OID", "-"),
            },
        )
        response.raise_for_status()
        return response.json()["jwt"]
