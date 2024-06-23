"""AbuseIPDB integration.

Authentication method: Token-based

Requires: secret named `abuseipdb` with key `ABUSEIPDB_API_KEY`

References: https://docs.abuseipdb.com/

Supported APIs:

```python
analyze_ip_address = {
    "endpoint": "/v2/check",
    "method": "GET",
    "ocsf_schema": "",
    "reference": "https://docs.abuseipdb.com/#check-endpoint",
}
```
"""

import os
from typing import Annotated, Any

import httpx

from tracecat.registry import Field, registry

ABUSEIPDB_BASE_URL = "https://api.abuseipdb.com/api"


@registry.register(
    default_title="Analyze IP Address",
    description="Analyze an IP address using AbuseIPDB.",
    display_group="AbuseIPDB",
    namespace="integrations.abuseipdb",
    secrets=["abuseipdb"],
)
async def analyze_ip_address(
    ip_address: Annotated[str, Field(..., description="The IP address to analyze")],
) -> dict[str, Any]:
    headers = {
        "Accept": "application/json",
        "Key": os.environ["ABUSEIPDB_API_KEY"],
    }
    async with httpx.AsyncClient(base_url=ABUSEIPDB_BASE_URL) as client:
        response = await client.get(
            "/v2/check", headers=headers, params={"ipAddress": ip_address}
        )
        response.raise_for_status()  # Raise an exception for HTTP errors
        return response.json()
