"""Emailrep integration.

Authentication method: Token-based

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
from typing import Any

import httpx

EMAILREP_BASE_URL = "https://emailrep.io"


def create_emailrep_client() -> httpx.AsyncClient:
    EMAILREP_API_KEY = os.getenv("EMAILREP_API_KEY")
    if EMAILREP_API_KEY is None:
        raise ValueError("EMAILREP_API_KEY is not set")
    headers = {"User-Agent": "tracecat-client", "Key": EMAILREP_API_KEY}
    return httpx.Client(base_url=EMAILREP_BASE_URL, headers=headers)


async def analyze_email(email: str) -> dict[str, Any]:
    async with create_emailrep_client() as client:
        response = await client.get(f"/email/{email}")
        response.raise_for_status()
        return response.json()
