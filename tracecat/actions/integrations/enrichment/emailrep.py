"""Emailrep integration.

Authentication method: Token-based

Requires: A secret named `emailrep` with key `EMAILREP_API_KEY`

References: https://docs.sublimesecurity.com/reference/get_-email

Supported APIs:

```python
analyze_email = {
    "endpoint": "/email",
    "method": "GET",
    "ocsf_schema": "",
}
```
"""

import os
from typing import Annotated, Any

import httpx

from tracecat.registry import Field, RegistrySecret, registry

EMAILREP_BASE_URL = "https://emailrep.io"


emailrep_secret = RegistrySecret(name="emailrep", keys=["EMAILREP_API_KEY"])
"""Emailrep secret.

Secret
------
- name: `emailrep`
- keys:
    - `EMAILREP_API_KEY`

Example Usage
-------------
Environment variable:
>>> os.environ["EMAILREP_API_KEY"]

Expression:
>>> ${{ SECRETS.emailrep.EMAILREP_API_KEY }}
"""


def create_emailrep_client() -> httpx.AsyncClient:
    EMAILREP_API_KEY = os.getenv("EMAILREP_API_KEY")
    if EMAILREP_API_KEY is None:
        raise ValueError("EMAILREP_API_KEY is not set")
    headers = {"User-Agent": "tracecat-client", "Key": EMAILREP_API_KEY}
    return httpx.AsyncClient(base_url=EMAILREP_BASE_URL, headers=headers)


@registry.register(
    default_title="Analyze email",
    description="Analyze an email address using Emailrep.",
    display_group="Emailrep",
    namespace="integrations.emailrep",
    secrets=[emailrep_secret],
)
async def analyze_email(
    email: Annotated[str, Field(..., description="The email address to analyze")],
) -> dict[str, Any]:
    async with create_emailrep_client() as client:
        response = await client.get(f"/email/{email}")
        response.raise_for_status()
        return response.json()
