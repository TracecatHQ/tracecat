"""Limacharlie authentication.

Docs: https://docs.limacharlie.io/apidocs/introduction
"""

import httpx

from tracecat_registry import RegistrySecret, registry, secrets

LIMACHARLIE_AUTH_URL = "https://jwt.limacharlie.io"


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
    default_title="Get Limacharlie JWT Token",
    description="Get a JWT token for Limacharlie API calls.",
    namespace="integrations.limacharlie",
    secrets=[limacharlie_secret],
)
async def get_jwt_token() -> str:
    secret = await secrets.get("limacharlie")
    async with httpx.AsyncClient() as client:
        response = await client.get(
            LIMACHARLIE_AUTH_URL,
            params={
                "uid": secret["LIMACHARLIE_UID"],
                "secret": secret["LIMACHARLIE_SECRET"],
                "oid": secret.get("LIMACHARLIE_OID", "-"),
            },
        )
        response.raise_for_status()
        return response.json()["jwt"]
