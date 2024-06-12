"""Pulsedive integration.

Authentication method: Token-based

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
from typing import Any

import httpx

PULSEDIVE_BASE_URL = "https://pulsedive.com/api/"


def create_pulsedive_client() -> httpx.AsyncClient:
    PULSEDIVE_API_KEY = os.getenv("PULSEDIVE_API_KEY")
    if PULSEDIVE_API_KEY is None:
        raise ValueError("PULSEDIVE_API_KEY is not set")
    client = httpx.AsyncClient(
        base_url=PULSEDIVE_BASE_URL, params={"key": PULSEDIVE_API_KEY}
    )
    return client


async def analyze_url(url: str) -> dict[str, Any]:
    params = {"q": url, "type": "url", "limit": 1}
    async with create_pulsedive_client() as client:
        response = await client.get("/explore.php", params=params)
        response.raise_for_status()
        return response.json()


async def analyze_ip_address(ip_address: str) -> dict[str, Any]:
    try:
        ip_obj = ipaddress.ip_address(ip_address)
    except ValueError as err:
        raise ValueError("Invalid IP address format") from err
    else:
        ioc_type = "ip" if ip_obj.version == 4 else "ipv6"
        params = {"q": ip_address, "type": ioc_type, "limit": 1}

    async with create_pulsedive_client() as client:
        response = await client.get("/explore.php?", params=params)
        response.raise_for_status()
        return response.json()
