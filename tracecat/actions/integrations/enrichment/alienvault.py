"""AlienVault OTX integration.

Authentication method: Token-based

Requires: OTX_API_KEY

References: https://otx.alienvault.com/assets/static/external_api.html

Supported APIs:

```python
analyze_url = {
    "endpoint": "/v1/indicators/url/{url}/general",
    "method": "GET",
    "ocsf_schema": "",
}
analyze_ip_address = {
    "endpoint": [
        "/v1/indicators/Ipv4/{ip_address}/general",
        "/v1/indicators/Ipv6/{ip_address}/general"
    ],
    "method": "GET",
    "ocsf_schema": "",
}
analyze_malware_sample = {
    "endpoint": "/v3/file/{file_hash}/general",
    "method": "GET",
    "ocsf_schema": "",
}
```
"""

import ipaddress
import os
from typing import Annotated, Any

import httpx

from tracecat.registry import Field, registry

# Base URL for AlienVault OTX API
OTX_BASE_URL = "https://otx.alienvault.com/api"


# Function to create an HTTPX async client for AlienVault OTX
def create_alienvault_client() -> httpx.AsyncClient:
    OTX_API_KEY = os.getenv("OTX_API_KEY")
    if OTX_API_KEY is None:
        raise ValueError("OTX_API_KEY is not set")
    client = httpx.AsyncClient(
        base_url=OTX_BASE_URL,
        headers={"X-OTX-API-KEY": OTX_API_KEY},
    )
    return client


@registry.register(
    description="Analyze a URL using AlienVault OTX.",
    namespace="alienvault",
)
async def analyze_url(
    url: Annotated[str, Field(..., description="The URL to analyze")],
) -> dict[str, Any]:
    async with create_alienvault_client() as client:
        response = await client.get(f"/v1/indicators/url/{url}")
        response.raise_for_status()
        return response.json()


@registry.register(
    description="Analyze an IP address using AlienVault OTX.",
    namespace="alienvault",
)
async def analyze_ip_address(
    ip_address: Annotated[str, Field(..., description="The IP address to analyze")],
) -> dict[str, Any]:
    try:
        ip_obj = ipaddress.ip_address(ip_address)
        version = "v4" if ip_obj.version == 4 else "v6"
    except ValueError as err:
        raise ValueError("Invalid IP address format") from err

    async with create_alienvault_client() as client:
        response = await client.get(f"/v1/indicators/IPv{version}/{ip_address}/general")
        response.raise_for_status()
        return response.json()


@registry.register(
    description="Analyze a malware sample using AlienVault OTX.",
    namespace="alienvault",
)
async def analyze_malware_sample(
    file_hash: Annotated[
        str, Field(..., description="The hash of the malware sample to analyze")
    ],
) -> dict[str, Any]:
    async with create_alienvault_client() as client:
        response = await client.get(f"/v1/indicators/file/{file_hash}/general")
        response.raise_for_status()
        return response.json()
