"""VirusTotal integration.

Authentication method: Token-based

Requires: A secret named `virustotal` with key `VT_API_KEY`.

References: https://docs.virustotal.com/reference/overview#most-popular-api-endpoints

Supported APIs:

```python
analyze_url = {
    "endpoint": "/v3/urls/{url_id}",
    "method": "GET",
    "ocsf_schema": "",
    "reference": "https://docs.virustotal.com/reference/url-object"
}
analyze_ip_address = {
    "endpoint": "/v3/ip_addresses/{ip_address}",
    "method": "GET",
    "ocsf_schema": "",
    "reference": "https://docs.virustotal.com/reference/ip-object"
}
analyze_malware_sample = {
    "endpoint": "/v3/files/{hash}",
    "method": "GET",
    "ocsf_schema": "",
    "reference": "https://docs.virustotal.com/reference/file-object"
}
```
"""

import base64
import os
from typing import Annotated, Any

import httpx

from tracecat.registry import Field, registry

VT_BASE_URL = "https://www.virustotal.com/api/"


def create_virustotal_client() -> httpx.AsyncClient:
    VT_API_KEY = os.getenv("VT_API_KEY")
    if VT_API_KEY is None:
        raise ValueError("VT_API_KEY is not set")
    client = httpx.AsyncClient(
        base_url=VT_BASE_URL,
        headers={"x-apikey": VT_API_KEY},
    )
    return client


@registry.register(
    description="Analyze a URL using VirusTotal.",
    namespace="integrations.enrich.virustotal",
    secrets=["virustotal"],
    default_title="VirusTotal",
    display_group="Enrichment",
)
async def analyze_url(
    url: Annotated[str, Field(..., description="The URL to analyze")],
) -> dict[str, Any]:
    url_id = base64.urlsafe_b64encode(url.encode()).decode().strip("=")
    async with create_virustotal_client() as client:
        response = await client.get(f"/v3/urls/{url_id}")
        response.raise_for_status()
        return response.json()


@registry.register(
    description="Analyze an IP address using VirusTotal.",
    namespace="virustotal",
)
async def analyze_ip_address(
    ip_address: Annotated[str, Field(..., description="The IP address to analyze")],
) -> dict[str, Any]:
    async with create_virustotal_client() as client:
        response = await client.get(f"/v3/ip_addresses/{ip_address}")
        response.raise_for_status()
        return response.json()


@registry.register(
    description="Analyze a malware sample using VirusTotal.",
    namespace="virustotal",
)
async def analyze_malware_sample(
    file_hash: Annotated[
        str, Field(..., description="The hash of the malware sample to analyze")
    ],
) -> dict[str, Any]:
    async with create_virustotal_client() as client:
        response = await client.get(f"/v3/files/{file_hash}")
        response.raise_for_status()
        return response.json()
