"""Pulsedive integration.

Authentication method: Token-based

Requires: A secret named `pulsedive` with key `PULSEDIVE_API_KEY`.

References:
- https://pulsedive.com/api/explore
- https://pulsedive.com/explore/

Supported APIs:

```python
analyze_url = {
    "endpoint": "/explore.php?",
    "method": "GET",
    "ocsf_schema": "",
    "reference": "https://pulsedive.com/api/explore"
}
analyze_ip_address = {
    "endpoint": "/explore.php?",
    "method": "GET",
    "ocsf_schema": "",
    "reference": "https://pulsedive.com/api/explore"
}
```

Note: the explore endpoint is a general search API. However, to keep unified API usage consistent, we limit the search to 1 result.
"""

import ipaddress
import os
from typing import Annotated, Any

import httpx

from tracecat.registry import Field, RegistrySecret, registry

PULSEDIVE_BASE_URL = "https://pulsedive.com/api/"

pulsedive_secret = RegistrySecret(name="pulsedive", keys=["PULSEDIVE_API_KEY"])
"""Pulsedive secret.

Secret
------
- name: `pulsedive`

- keys:
    - `PULSEDIVE_API_KEY`

Example Usage
-------------
Environment variable:
>>> os.environ["PULSEDIVE_API_KEY"]

Expression:
>>> ${{ SECRETS.pulsedive.PULSEDIVE_API_KEY }}
"""


def create_pulsedive_client() -> httpx.AsyncClient:
    PULSEDIVE_API_KEY = os.getenv("PULSEDIVE_API_KEY")
    if PULSEDIVE_API_KEY is None:
        raise ValueError("PULSEDIVE_API_KEY is not set")
    client = httpx.AsyncClient(
        base_url=PULSEDIVE_BASE_URL, params={"key": PULSEDIVE_API_KEY}
    )
    return client


@registry.register(
    default_title="Analyze URL",
    description="Analyze a URL using Pulsedive.",
    display_group="Pulsedive",
    namespace="integrations.pulsedive",
    secrets=[pulsedive_secret],
)
async def analyze_url(
    url: Annotated[str, Field(..., description="The URL to analyze")],
) -> dict[str, Any]:
    params = {"q": url, "type": "url", "limit": 1}
    async with create_pulsedive_client() as client:
        response = await client.get("/explore.php", params=params)
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Analyze IP address",
    description="Analyze an IP address using Pulsedive.",
    display_group="Pulsedive",
    namespace="integrations.pulsedive",
    secrets=[pulsedive_secret],
)
async def analyze_ip_address(
    ip_address: Annotated[str, Field(..., description="The IP address to analyze")],
) -> dict[str, Any]:
    try:
        ip_obj = ipaddress.ip_address(ip_address)
    except ValueError as err:
        raise ValueError("Invalid IP address format") from err
    else:
        ioc_type = "ip" if ip_obj.version == 4 else "ipv6"
        params = {"q": ip_address, "type": ioc_type, "limit": 1}

    async with create_pulsedive_client() as client:
        response = await client.get("/explore.php", params=params)
        response.raise_for_status()
        return response.json()
